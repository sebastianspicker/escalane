from __future__ import annotations

try:
    from tests.assertions import expect
except ModuleNotFoundError:
    from assertions import expect

from alarm_broker.api.template_loader import load_template


def test_load_template_reads_packaged_ack_html() -> None:
    template = load_template("ack.html")

    expect("<title>${title}</title>" in template.template)


def test_load_template_reads_packaged_admin_html() -> None:
    template = load_template("admin.html")

    expect("Mission Control - Alarm Broker" in template.template)
