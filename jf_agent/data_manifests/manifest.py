import gzip
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
import json
from typing import Optional, TypeVar

import requests

from jf_ingest import logging_helper


logger = logging.getLogger(__name__)

IManifest = TypeVar('IManifest', bound='Manifest')


class MalformedJSONRepresentation(Exception):
    pass


class ManifestSource(Enum):
    remote = 'REMOTE'


class ManifestType(Enum):
    git = 'GitDataManifest'
    jira = 'JiraDataManifest'


@dataclass
class Manifest(ABC):
    company: str
    data_source: ManifestSource
    date: datetime = field(init=False)
    manifest_type: str = field(init=False)
    _module_name: str = field(init=False)

    @staticmethod
    def _serialize_datetime(date: datetime) -> str:
        return date.strftime("%Y%m%dT%H:%M:%S")

    def get_date_str(self) -> str:
        return self._serialize_datetime(self.date)

    def _serialize_manifest(self, value):
        if isinstance(value, datetime):
            return value.isoformat()
        elif isinstance(value, Manifest):
            return value.__dict__
        elif isinstance(value, Enum):
            return value.value
        else:
            return str(value)

    def __post_init__(self):
        self.date = datetime.utcnow()
        self.manifest_type = self.__class__.__name__
        # We have to do some module name injection hackery here to make manifests
        # generated here (in the agent) compatible with manifests generated in our
        # private repository
        self._module_name = (
            'core.data_manifests.jira.manifest'
            if 'jira' in self.__class__.__module__
            else 'core.data_manifests.git.manifest'
        )

    # To be filled out by child classes
    # A globally unique ID for a manifest
    # For git/jira, this includes the manifest name,
    # datetime, and manifest source (remote/local)
    def get_unique_key(self):
        return f'{self.get_manifest_full_name()}_{self.data_source.value}_{self.get_date_str()}'

    @abstractmethod
    # A manifest's full name. This does not guarantee uniqueness like get_unique_key does.
    # This is used to identify manifests between different data sources,
    # i.e. is this pull request from Local the same one in Remote?
    def get_manifest_full_name(self):
        pass

    def __str__(self):
        return self.get_manifest_full_name()

    def __hash__(self):
        return hash(repr(self.get_manifest_full_name()))

    def __eq__(self, __o: IManifest) -> bool:
        return self.get_manifest_full_name() == __o.get_manifest_full_name()

    def to_json_str(self) -> str:
        return json.dumps(self.__dict__, indent=2, default=self._serialize_manifest)

    def to_local_file(
        self, file_name: Optional[str] = None, verbose_logging: Optional[bool] = True
    ) -> None:
        file_name = file_name or f'./{self.get_unique_key()}.json'
        if verbose_logging:
            logger.info(f'Writing file data to {file_name}')
        with open(file_name, 'w') as f:
            f.write(self.to_json_str())

    def upload_to_s3(self, jellyfish_api_base: str, jellyfish_api_token: str) -> None:
        headers = {'Jellyfish-API-Token': jellyfish_api_token, 'content-encoding': 'gzip'}

        logger.info(f'Attempting to upload {self.get_unique_key()} manifest to s3...')

        r = requests.post(
            f'{jellyfish_api_base}/endpoints/agent/upload_manifest',
            headers=headers,
            data=gzip.compress(bytes(self.to_json_str(), encoding='utf-8')),
        )
        r.raise_for_status()

        logger.info(f'Successfully uploaded {self.get_unique_key()} manifest to s3!')
