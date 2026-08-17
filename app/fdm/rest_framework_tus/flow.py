from viewflow.fsm import State

from fdm.rest_framework_tus import signals, states


class UploadState(object):
    """
    Choices-like class for viewflow.fsm State.
    Provides the state values as attributes.
    """

    INITIAL = states.INITIAL
    RECEIVING = states.RECEIVING
    SAVING = states.SAVING
    DONE = states.DONE


class UploadFlow(object):
    """
    FSM flow for managing TUS upload state transitions.
    Wraps an AbstractUpload model instance.
    """

    state = State(UploadState, default=UploadState.INITIAL)

    def __init__(self, upload):
        self.upload = upload

    @state.getter()
    def _get_state(self):
        return self.upload.state

    @state.setter()
    def _set_state(self, value):
        self.upload.state = value

    @state.on_success()
    def _on_transition(self, descriptor, source, target):
        self.upload.save()

    @state.transition(source=UploadState.INITIAL, target=UploadState.RECEIVING)
    def start_receiving(self):
        if not self.upload._temporary_file_exists():
            raise ValueError("Temporary file does not exist.")
        signals.receiving.send(sender=self.upload.__class__, instance=self.upload)

    @state.transition(source=UploadState.RECEIVING, target=UploadState.SAVING)
    def start_saving(self):
        if not self.upload.is_complete():
            raise ValueError("Upload is not complete.")
        signals.saving.send(sender=self.upload.__class__, instance=self.upload)

    @state.transition(source=UploadState.SAVING, target=UploadState.DONE)
    def finish(self):
        signals.finished.send(sender=self.upload.__class__, instance=self.upload)
