"""Business-owned transactional persistence; never write Doclib tables."""

from .sqlite import BusinessStore, BusinessStoreError

__all__ = ["BusinessStore", "BusinessStoreError"]
