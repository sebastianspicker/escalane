"""Connector package for external service integrations.

This package provides connectors for various external services:
- Zammad: Ticket management
- SendXMS: SMS notifications
- Signal: Group messaging
- Mock: Simulation mode connectors
"""

from escalane.connectors.base import BaseConnector, BaseConnectorConfig
from escalane.connectors.mock import (
    MockNotificationStore,
    MockSendXmsClient,
    MockSignalClient,
    MockZammadClient,
    get_mock_store,
)
from escalane.connectors.sendxms import SendXmsClient, SendXmsConfig
from escalane.connectors.signal import SignalClient, SignalConfig
from escalane.connectors.zammad import ZammadClient, ZammadConfig

__all__ = [
    # Base classes
    "BaseConnector",
    "BaseConnectorConfig",
    # Zammad
    "ZammadClient",
    "ZammadConfig",
    # SendXMS
    "SendXmsClient",
    "SendXmsConfig",
    # Signal
    "SignalClient",
    "SignalConfig",
    # Mock/Simulation
    "MockZammadClient",
    "MockSendXmsClient",
    "MockSignalClient",
    "MockNotificationStore",
    "get_mock_store",
]
