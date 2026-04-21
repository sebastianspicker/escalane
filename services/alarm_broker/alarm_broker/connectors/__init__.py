"""Connector package for external service integrations.

This package provides connectors for various external services:
- Zammad: Ticket management
- SendXMS: SMS notifications
- Signal: Group messaging
- Mock: Simulation mode connectors
"""

from alarm_broker.connectors.base import BaseConnector, BaseConnectorConfig
from alarm_broker.connectors.mock import (
    MockNotificationStore,
    MockSendXmsClient,
    MockSignalClient,
    MockZammadClient,
    get_mock_store,
)
from alarm_broker.connectors.sendxms import SendXmsClient, SendXmsConfig
from alarm_broker.connectors.signal import SignalClient, SignalConfig
from alarm_broker.connectors.zammad import ZammadClient, ZammadConfig

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
