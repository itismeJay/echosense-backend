from enum import Enum


class LanguageCode(str, Enum):
    FILIPINO = "fil"
    CEBUANO = "ceb"
    ENGLISH = "en"
    MIXED = "mixed"
    UNKNOWN = "unknown"


class DictionaryLanguageCode(str, Enum):
    FILIPINO = "fil"
    CEBUANO = "ceb"
    ENGLISH = "en"
