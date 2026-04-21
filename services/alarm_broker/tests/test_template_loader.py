from __future__ import annotations

from alarm_broker.api.template_loader import load_template


def test_load_template_reads_packaged_ack_html() -> None:
    template = load_template("ack.html")

    assert "<title>${title}</title>" in template.template


def test_load_template_reads_packaged_admin_html() -> None:
    template = load_template("admin.html")

    assert "Mission Control - Alarm Broker" in template.template
