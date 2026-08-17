import os
import time

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.utils import timezone
from django_fernet.fernet import FernetTextFieldData

from fdm.core.helpers import set_request_for_user
from fdm.projects.models import Project
from fdm.storages.models.models import DynamicStorage
from fdm.uploads.models import UploadsDataset


class Command(BaseCommand):
    help = "Creates a structure with Storage, Project, Dataset, Version and a reference Version File"

    @staticmethod
    def create_test_file(base_path, reference_path, size_mb=1):
        """Create a test file of specified size in MB."""
        # Create the full path by joining the base path with reference path
        full_path = os.path.join(base_path, reference_path)

        # Create directory structure for the reference file
        ref_dir = os.path.dirname(full_path)
        if not os.path.exists(ref_dir):
            os.makedirs(ref_dir, exist_ok=True)

        # Calculate bytes for 1MB (1024*1024 bytes)
        size_bytes = size_mb * 1024 * 1024

        try:
            with open(full_path, "wb") as f:
                # Write data in chunks of 1KB for memory efficiency
                chunk_size = 1024  # 1KB
                for _ in range(size_bytes // chunk_size):
                    f.write(b"0" * chunk_size)

                # Write any remaining bytes
                remaining_bytes = size_bytes % chunk_size
                if remaining_bytes > 0:
                    f.write(b"0" * remaining_bytes)

            return True, full_path
        except Exception as e:
            return False, str(e)

    def handle(self, *args, **options):
        # Fixed defaults
        timestamp = int(time.time())
        name_prefix = f"Demo {timestamp}"
        reference_path = f"path/to/reference/file_{timestamp}.txt"
        dss_path = "dssfs/container/private-dss0001"
        current_time = timezone.now()

        # Create the proper media path by joining MEDIA_ROOT with dss_path
        media_path = os.path.join(settings.MEDIA_ROOT, dss_path)

        # Get admin user for project membership
        User = get_user_model()
        admin_user = User.objects.filter(is_superuser=True, can_create_projects=True).first()
        if not admin_user:
            self.stdout.write(self.style.ERROR("❌ No admin user found. Please create one first."))
            return

        set_request_for_user(admin_user)

        self.stdout.write(
            self.style.SUCCESS(f"Creating reference file structure with prefix '{name_prefix}'..."),
        )

        # Create the physical directory structure for the DSS storage in media path
        self.stdout.write(f"Creating directory structure for DSS storage at '{media_path}'...")
        if not os.path.exists(media_path):
            try:
                os.makedirs(media_path, exist_ok=True)
                self.stdout.write(self.style.SUCCESS(f"✓ Directory structure created at '{media_path}'"))
            except Exception as e:
                self.stdout.write(
                    self.style.WARNING(
                        "⚠️ Could not create directory '{}': {}\n"
                        "Will continue with storage creation but mounting might fail.".format(media_path, str(e)),
                    ),
                )
        else:
            self.stdout.write(self.style.SUCCESS(f"✓ Directory '{media_path}' already exists"))

        # Create a 1MB test file in the media path
        self.stdout.write("Creating 1MB test file in DSS path...")
        success, result = self.create_test_file(media_path, reference_path)
        if success:
            self.stdout.write(self.style.SUCCESS(f"✓ Created 1MB test file at '{result}'"))
        else:
            self.stdout.write(self.style.WARNING(f"⚠️ Could not create test file: {result}"))

        # 1. Create a storage with private_dss type
        self.stdout.write("Creating storage...")
        storage = DynamicStorage.objects.create(
            name=f"{name_prefix} Private DSS Storage",
            storage_type="private_dss",
            default=False,
            approved=True,
            mounted=True,
            created_by=admin_user,
            last_modified_by=admin_user,
            creation_date=current_time,
            last_modification_date=current_time,
        )

        # Set up encrypted path for the storage
        field_data = FernetTextFieldData()
        field_data.encrypt(dss_path, settings.SECRET_KEY)
        storage.local_private_dss_path_encrypted = field_data
        storage.save()
        self.stdout.write(self.style.SUCCESS(f"✓ Storage created: {storage}"))

        # 2. Create a project with required user fields
        self.stdout.write("Creating project...")
        project = Project.objects.create(
            name=f"{name_prefix} Project",
            created_by=admin_user,
            last_modified_by=admin_user,
            creation_date=current_time,
            last_modification_date=current_time,
        )
        self.stdout.write(self.style.SUCCESS(f"✓ Project created: {project}"))

        # 3. Get the default folder created with the project and connect it to the storage
        folder = project.folders.first()
        folder.storage = storage
        folder.save()
        self.stdout.write(self.style.SUCCESS(f"✓ Folder associated with storage: {folder}"))

        # 4. Create a dataset
        self.stdout.write("Creating dataset...")
        dataset = UploadsDataset.objects.create(
            name=f"{name_prefix} Dataset",
            folder=folder,
            publication_date=current_time,
            created_by=admin_user,
            last_modified_by=admin_user,
            creation_date=current_time,
            last_modification_date=current_time,
        )
        self.stdout.write(self.style.SUCCESS(f"✓ Dataset created: {dataset.pk}"))
