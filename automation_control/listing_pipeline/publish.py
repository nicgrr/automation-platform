"""Structural placeholder for the future eBay Sell API integration.

Deliberately not implemented and not imported by cli.py's `run` command, so
there is no code path today that reaches eBay's write APIs. When the Sell API
integration is added, this becomes the single call site, and it must require
an automation_control.models.Approval record with
status == ApprovalStatus.APPROVED before making any eBay write call — see
automation_control/approvals.py for the existing approve/reject mechanism
this should plug into rather than inventing a parallel one.
"""

from typing import NoReturn

from .models import Listing


class PublishNotImplemented(Exception):
    pass


def publish(listing: Listing) -> NoReturn:
    raise PublishNotImplemented("eBay Sell API publishing is not implemented yet")
