"""PhotoProof: modular digital image forgery detection and localization."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version(__name__)
except PackageNotFoundError:  # running from a source tree without installation
    __version__ = "0+unknown"
