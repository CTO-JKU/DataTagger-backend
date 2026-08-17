import json
import logging

from asgiref.sync import async_to_sync
from rest_framework.exceptions import ValidationError

from fdm.core.helpers import get_content_type_for_model
from fdm.folders.models import Folder
from fdm.projects.models import Project, ProjectMembership
from fdm.uploads.models import UploadsDataset
from fdm.websockets.consumers.core import AuthenticatedJsonWebsocketConsumer

logger = logging.getLogger(__name__)


class WebsocketConsumer(AuthenticatedJsonWebsocketConsumer):
    ALLOWED_ACTIONS = (
        "subscribe",
        "unsubscribe",
        "unsubscribe_all",
    )

    AVAILABLE_MODELS = (
        Project,
        Folder,
        UploadsDataset,
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.group_names = []

    def authentication_success(self, user):
        group_name = "lock_status_changes"
        self.group_names.append(group_name)
        logger.info(f"Subscribing to {group_name}")
        async_to_sync(self.channel_layer.group_add)(group_name, self.channel_name)
        self.send_json(
            {
                "type": "lock_status_changes_subscribe_success",
                "data": {},
            },
        )

    def receive_json_authenticated(self, content, **kwargs):
        if "action" in content:
            action = content["action"].lower()
            if action not in self.ALLOWED_ACTIONS:
                logger.info(f"Method {action} not in allowed actions")
            else:
                if hasattr(self, action) and callable(getattr(self, action)):
                    action_method = getattr(self, action)
                    action_method(content)
                else:
                    logger.info(f"Method {action} not found")
        else:
            logger.info("Please use the 'action' attribute in your JSON")

    def connect(self, **kwargs):
        self.accept()

    def disconnect(self, *args, **kwargs):
        logger.info("Disconnecting...")
        for group_name in self.group_names:
            logger.info(f"Removing channel from group {group_name}")
            async_to_sync(self.channel_layer.group_discard)(group_name, self.channel_name)

    def check_params(self, data):
        if "content_type" not in data:
            raise ValidationError("Expected 'content_type' to be set")
        if "pk" not in data:
            raise ValidationError("Expected 'pk' to be set")
        content_type = data["content_type"].lower()
        pk = data["pk"]
        available_models = {
            get_content_type_for_model(model).lower().split(".")[1]: model for model in self.AVAILABLE_MODELS
        }
        if content_type in available_models:
            model = available_models[content_type]
            try:
                element = model.objects.get(pk=pk)
                if self.check_project_membership(self, model, element):
                    group_name = f"{content_type}_{pk}"
                    return element, group_name
            except model.DoesNotExist:
                logger.error(f"Could not find object with pk {pk} for {model}")
        logger.info(f"Could not find {content_type} in {available_models}")
        return None, None

    def subscribe(self, data):
        element, group_name = self.check_params(data)
        if element and group_name:
            if group_name not in self.group_names:
                self.group_names.append(group_name)
            logger.info(f"Subscribing to {group_name}")
            async_to_sync(self.channel_layer.group_add)(group_name, self.channel_name)
            self.send_json(
                {
                    "type": "subscribe_success",
                    "data": {
                        "content_type": f"{data['content_type'].lower()}",
                        "pk": f"{data['pk']}",
                    },
                },
            )
            return
        logger.info(f"Can not subscribe to {data}")

    def unsubscribe(self, data):
        element, group_name = self.check_params(data)
        if element and group_name:
            if group_name in self.group_names:
                self.group_names.remove(group_name)
                logger.info(f"Unsubscribing from {group_name}")
                async_to_sync(self.channel_layer.group_discard)(group_name, self.channel_name)
                self.send_json(
                    {
                        "type": "unsubscribe_success",
                        "data": {
                            "content_type": f"{data['content_type'].lower()}",
                            "pk": f"{data['pk']}",
                        },
                    },
                )
            else:
                logger.info(f"Group_name {group_name} is not in self.group_names")
        else:
            logger.info("Trying to unsubscribe from an element that does not exist...")

    def unsubscribe_all(self, data):
        logger.info("Unsubscribing from all groups")
        for group_name in self.group_names:
            logger.info(f"Removing channel from group {group_name}")
            async_to_sync(self.channel_layer.group_discard)(group_name, self.channel_name)
        self.send_json({"type": "unsubscribe_all_success", "data": {}})

    @staticmethod
    def check_project_membership(self, model, element):
        if model == Project:
            logger.info(f"Checking project membership for {model} {element} {self.scope['user']}")
            return ProjectMembership.objects.filter(
                member=self.scope["user"],
                project=element,
            ).exists()
        # allow subscribing to other allowed models for now for testing
        return True

    def parser_status_changed(self, event):
        data = event["data"]
        event_type = event["type"]
        self.send(text_data=json.dumps({"type": event_type, "data": data}))

    def lock_status_changed(self, event):
        data = event["data"]
        event_type = event["type"]
        self.send(text_data=json.dumps({"type": event_type, "data": data}))

    def model_changed(self, event):
        data = event["data"]
        event_type = event["type"]
        self.send(text_data=json.dumps({"type": event_type, "data": data}))
