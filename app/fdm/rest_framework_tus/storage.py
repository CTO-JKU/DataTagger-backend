from abc import ABCMeta, abstractmethod

from django.core.files import File
from django.utils.module_loading import import_string

from fdm.rest_framework_tus import signals

from .settings import TUS_SAVE_HANDLER_CLASS


class AbstractUploadSaveHandler(metaclass=ABCMeta):
    def __init__(self, upload):
        self.upload = upload

    @abstractmethod
    def handle_save(self):
        pass

    def run(self):
        # Trigger state change
        flow = self.upload.get_flow()
        flow.start_saving()

        # Initialize saving
        self.handle_save()

    def finish(self):
        # Trigger signal
        signals.saved.send(sender=self.__class__, instance=self)

        # Finish
        flow = self.upload.get_flow()
        flow.finish()


class DefaultSaveHandler(AbstractUploadSaveHandler):
    destination_file_field = "uploaded_file"

    def handle_save(self):
        # Save temporary field to file field
        file_field = getattr(self.upload, self.destination_file_field)
        with open(self.upload.temporary_file_path, "rb") as f:
            file_field.save(self.upload.filename, File(f))

        self.finish()


def get_save_handler(import_path=None):
    return import_string(import_path or TUS_SAVE_HANDLER_CLASS)
