from dataclasses import dataclass
from typing import Callable

from fdm.file_parser.models import CsvValueParser, FileParser, validate_csv_extraction_rules_config
from fdm.uploads.models import UploadsVersionFile

CSV_VALUE_CONFIG_SCHEMA = {
    "type": "array",
    "minItems": 1,
    "items": {
        "type": "object",
        "required": [
            "row",
            "column",
            "key",
        ],
        "properties": {
            "row": {
                "type": "integer",
                "minimum": 0,
            },
            "column": {
                "type": "integer",
                "minimum": 0,
            },
            "key": {
                "oneOf": [
                    {
                        "type": "string",
                        "minLength": 1,
                    },
                    {
                        "type": "object",
                        "required": [
                            "row",
                            "column",
                        ],
                        "properties": {
                            "row": {
                                "type": "integer",
                                "minimum": 0,
                            },
                            "column": {
                                "type": "integer",
                                "minimum": 0,
                            },
                        },
                    },
                ],
            },
        },
    },
}


@dataclass(frozen=True)
class FileParserDefinition:
    parser_type: str
    label: str
    runner_class: type
    validate_config: Callable[[dict], None]
    config_schema: dict

    def is_eligible(self, file: UploadsVersionFile) -> bool:
        return self.runner_class(file).check_eligible_file()

    def serialize(self) -> dict:
        return {
            "parser_type": self.parser_type,
            "label": self.label,
            "config_schema": self.config_schema,
        }


PARSER_REGISTRY = {
    FileParser.Type.CSV_VALUE: FileParserDefinition(
        parser_type=FileParser.Type.CSV_VALUE,
        label=FileParser.Type.CSV_VALUE.label,
        runner_class=CsvValueParser,
        validate_config=validate_csv_extraction_rules_config,
        config_schema=CSV_VALUE_CONFIG_SCHEMA,
    ),
}


def get_parser_definition(parser_type: str) -> FileParserDefinition | None:
    return PARSER_REGISTRY.get(parser_type)


def get_available_parser_definitions() -> list[FileParserDefinition]:
    return list(PARSER_REGISTRY.values())


def get_configurable_parser_types() -> list[str]:
    return list(PARSER_REGISTRY.keys())
