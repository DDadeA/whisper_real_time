#! python3.7

import io
import speech_recognition as sr
from . import whisper
import torch

from datetime import datetime, timedelta
from queue import Queue
from tempfile import NamedTemporaryFile
from sys import platform


class WhisperRecognizer(whisper.WhisperModel):
    def __init__(self, model:str):
        '''
        model: huggingface model name
        '''

        self.phrase_time = None
        self.last_sample = bytes()
        self.data_queue = Queue()
        self.recorder = sr.Recognizer()
        self.recorder.energy_threshold = 1000
        self.recorder.dynamic_energy_threshold = False
        self.source = sr.Microphone(sample_rate=16000)
        self.record_timeout = 2
        self.phrase_timeout = 3
        self.temp_file = NamedTemporaryFile().name
        self.transcription = ['']
        
        
        # Important for linux users. 
        # Prevents permanent application hang and crash by using the wrong Microphone
        if 'linux' in platform:
            mic_name = 'pulse'
            if not mic_name or mic_name == 'list':
                print("Available microphone devices are: ")
                for index, name in enumerate(sr.Microphone.list_microphone_names()):
                    print(f"Microphone with name \"{name}\" found")   
                return
            else:
                for index, name in enumerate(sr.Microphone.list_microphone_names()):
                    if mic_name in name:
                        self.source = sr.Microphone(sample_rate=16000, device_index=index)
                        break
        else: pass
        
        
        # Load / Download model
        self.audio_model = whisper.WhisperModel(model, fp16_available=torch.cuda.is_available())
        
        with self.source:
            self.recorder.adjust_for_ambient_noise(self.source)

        def record_callback(_, audio:sr.AudioData) -> None:
            """
            Threaded callback function to recieve audio data when recordings finish.
            audio: An AudioData containing the recorded bytes.
            """
            # Grab the raw bytes and push it into the thread safe queue.
            data = audio.get_raw_data()
            self.data_queue.put(data)

        # Create a background thread that will pass us raw audio bytes.
        # We could do this manually but SpeechRecognizer provides a nice helper.
        self.recorder.listen_in_background(self.source, record_callback, phrase_time_limit=self.record_timeout)



    def get_sentence(self):
        self.now = datetime.utcnow()
        # Pull raw recorded audio from the queue.
        if not self.data_queue.empty():
            phrase_complete = False
            # If enough time has passed between recordings, consider the phrase complete.
            # Clear the current working audio buffer to start over with the new data.
            if self.phrase_time and self.now - self.phrase_time > timedelta(seconds=self.phrase_timeout):
                self.last_sample = bytes()
                phrase_complete = True
            # This is the last time we received new audio data from the queue.
            self.phrase_time = self.now

            # Concatenate our current audio data with the latest audio data.
            while not self.data_queue.empty():
                data = self.data_queue.get()
                self.last_sample += data

            # Use AudioData to convert the raw data to wav data.
            audio_data = sr.AudioData(self.last_sample, self.source.SAMPLE_RATE, self.source.SAMPLE_WIDTH)
            wav_data = io.BytesIO(audio_data.get_wav_data())

            # Write wav data to the temporary file as bytes.
            with open(self.temp_file, 'w+b') as f:
                f.write(wav_data.read())

            # Read the transcription.
            result = self.audio_model.transcribe(self.temp_file) # mod. move to the load section, fp16=torch.cuda.is_available())
            text = result['text'].strip()

            # If we detected a pause between recordings, add a new item to our transcripion.
            # Otherwise edit the existing one.
            if phrase_complete:
                self.transcription.append(text)
            elif not text==None:
                self.transcription[-1] = text

            return self.transcription
