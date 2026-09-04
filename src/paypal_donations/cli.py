"""Command Line Interface for the PayPal Donation Platform."""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional
import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from paypal_donations import __version__
from paypal_donations.email_service import EmailService
from paypal_donations.models import CurrencyCode, EmailReceiptPayload, RefundReceiptPayload
from paypal_donations.repository import DonorRepository

console = Console()


def get_repo(db_path: Optional[str] = None) -> DonorRepository:
    path = db_path or os.getenv("DATABASE_PATH", "storage/donations.sqlite3")
    return DonorRepository(db_path=path)


@click.group(invoke_without_command=True)
@click.version_option(version=__version__, prog_name="paypal-donations")
@click.pass_context
def main(ctx: click.Context) -> None:
    """PayPal Donation Platform CLI & Management Suite."""
    if ctx.invoked_subcommand is None:
        console.print(
            Panel.fit(
                f"[bold cyan]PayPal Donation Platform[/bold cyan] [green]v{__version__}[/green]\n"
                "Run [yellow]paypal-donations --help[/yellow] for available commands.",
                border_style="cyan",
            )
        )


@main.command(name="serve")
@click.option("--host", default="127.0.0.1", help="Server bind host.")
@click.option("--port", default=8000, type=int, help="Server bind port.")
@click.option("--reload", is_flag=True, default=False, help="Enable auto-reload for development.")
def serve_command(host: str, port: int, reload: bool) -> None:
    """Launch the FastAPI web server and interactive donation portal."""
    import uvicorn

    console.print(f"[bold green]Starting PayPal Donation Platform at[/bold green] [bold cyan]http://{host}:{port}[/bold cyan]")
    console.print("[dim]Press Ctrl+C to stop the server.[/dim]")
    uvicorn.run("paypal_donations.api:app", host=host, port=port, reload=reload)


@main.group(name="donations")
def donations_group() -> None:
    """Manage and inspect donation records and analytics."""
    pass


@donations_group.command(name="list")
@click.option("--limit", default=25, type=int, help="Maximum records to display.")
@click.option("--status", default=None, help="Filter by donation status (e.g. COMPLETED).")
@click.option("--db", default=None, help="Custom SQLite database path.")
def list_donations(limit: int, status: Optional[str], db: Optional[str]) -> None:
    """Display recent donor log records in a formatted table."""
    repo = get_repo(db)
    donations = repo.list_donations(limit=limit, status=status)

    if not donations:
        console.print("[yellow]No donation records found.[/yellow]")
        return

    table = Table(title=f"Donation Log (Latest {len(donations)})", border_style="cyan")
    table.add_column("Order ID", style="cyan", no_wrap=True)
    table.add_column("Date (UTC)", style="dim")
    table.add_column("Donor", style="bold")
    table.add_column("Email", style="magenta")
    table.add_column("Amount", justify="right", style="green")
    table.add_column("Status", style="bold")
    table.add_column("Receipt Sent", justify="center")

    for d in donations:
        name_display = f"{d.donor_name} (Anon)" if d.is_anonymous else d.donor_name
        status_color = "green" if d.status.value == "COMPLETED" else "yellow"
        receipt_icon = "[green]Yes[/green]" if d.receipt_sent else "[dim]No[/dim]"
        date_str = d.created_at.strftime("%Y-%m-%d %H:%M")
        table.add_row(
            d.order_id,
            date_str,
            name_display,
            d.donor_email,
            f"{d.amount:.2f} {d.currency.value}",
            f"[{status_color}]{d.status.value}[/{status_color}]",
            receipt_icon,
        )

    console.print(table)


@donations_group.command(name="stats")
@click.option("--db", default=None, help="Custom SQLite database path.")
def show_stats(db: Optional[str]) -> None:
    """Show aggregated donation financial metrics and donor counts."""
    repo = get_repo(db)
    stats = repo.get_statistics()

    total_usd = stats.total_amount_by_currency.get("USD", Decimal("0.00"))

    grid = Table.grid(expand=True, padding=(0, 2))
    grid.add_column(justify="center")
    grid.add_column(justify="center")
    grid.add_column(justify="center")
    grid.add_column(justify="center")

    grid.add_row(
        Panel(f"[bold green]${total_usd:.2f}[/bold green]", title="[cyan]Total Raised (USD)[/cyan]"),
        Panel(f"[bold cyan]{stats.total_donations}[/bold cyan]", title="[cyan]Total Donations[/cyan]"),
        Panel(f"[bold magenta]{stats.unique_donors}[/bold magenta]", title="[cyan]Unique Donors[/cyan]"),
        Panel(f"[bold yellow]${stats.average_amount:.2f}[/bold yellow]", title="[cyan]Avg Donation[/cyan]"),
    )

    console.print(Panel(grid, title="[bold]Donation Platform Statistics[/bold]", border_style="blue"))


@donations_group.command(name="export")
@click.option("--format", "export_format", default="csv", type=click.Choice(["csv", "json"]), help="Export format.")
@click.option("--output", "output_path", required=True, help="Target file path.")
@click.option("--db", default=None, help="Custom SQLite database path.")
def export_donations(export_format: str, output_path: str, db: Optional[str]) -> None:
    """Export the complete donation log to CSV or JSON."""
    repo = get_repo(db)
    if export_format == "csv":
        out = repo.export_csv(output_path)
    else:
        out = repo.export_json(output_path)
    console.print(f"[bold green]Successfully exported donation logs to:[/bold green] [bold cyan]{out}[/bold cyan]")


@donations_group.command(name="test-email")
@click.option("--recipient", required=True, help="Recipient email address.")
@click.option("--amount", default=25.0, type=float, help="Sample donation amount.")
@click.option("--name", default="Supporter Friend", help="Sample donor name.")
def test_email(recipient: str, amount: float, name: str) -> None:
    """Generate and dispatch a sample donation confirmation receipt."""
    mailer = EmailService(mode=os.getenv("EMAIL_MODE", "mock"))
    payload = EmailReceiptPayload(
        receipt_id=f"TEST-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}",
        order_id="TEST-ORDER-12345",
        capture_id="TEST-CAPTURE-67890",
        donor_name=name,
        donor_email=recipient,
        amount=Decimal(f"{amount:.2f}"),
        currency=CurrencyCode.USD,
        date_formatted=datetime.now(timezone.utc).strftime("%B %d, %Y at %H:%M UTC"),
        dedication="Test sample dedication for demonstration",
        org_name="Open Impact Foundation",
        org_email="donations@openimpact.org",
        org_tax_id="501(c)(3)-849201",
        org_website="https://openimpact.org",
    )
    ok = mailer.send_receipt(payload)
    if ok:
        console.print(f"[bold green]Confirmation receipt successfully generated & dispatched to:[/bold green] [cyan]{recipient}[/cyan]")
        if mailer.dispatched_receipts:
            console.print(f"[dim]Mock receipt file saved at: {mailer.dispatched_receipts[-1]['file_path']}[/dim]")
    else:
        console.print("[bold red]Failed to dispatch confirmation receipt.[/bold red]")


@donations_group.command(name="refund")
@click.option("--order", "order_id", required=True, help="PayPal Order ID to refund.")
@click.option("--reason", default="Donor requested refund", help="Refund reason.")
@click.option("--db", default=None, help="Custom SQLite database path.")
def refund_donation_cli(order_id: str, reason: str, db: Optional[str]) -> None:
    """Record a donation refund and issue a refund receipt notice."""
    repo = get_repo(db)
    donation = repo.get_by_order_id(order_id)
    if not donation:
        console.print(f"[bold red]Donation with Order ID {order_id} not found.[/bold red]")
        sys.exit(1)

    if donation.status.value == "REFUNDED":
        console.print(f"[yellow]Donation {order_id} has already been marked as REFUNDED.[/yellow]")
        return

    updated = repo.record_refund(order_id=order_id, reason=reason)
    if not updated:
        console.print(f"[bold red]Failed to record refund for {order_id}.[/bold red]")
        sys.exit(1)

    mailer = EmailService(mode=os.getenv("EMAIL_MODE", "mock"))
    refund_id = f"REF-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
    payload = RefundReceiptPayload(
        receipt_id=f"REF-{order_id[-6:]}",
        order_id=order_id,
        capture_id=donation.capture_id or order_id,
        refund_id=refund_id,
        donor_name=donation.donor_name,
        donor_email=donation.donor_email,
        amount=donation.amount,
        currency=donation.currency,
        date_formatted=datetime.now(timezone.utc).strftime("%B %d, %Y at %H:%M UTC"),
        reason=reason,
        org_name=os.getenv("ORG_NAME", "Open Impact Foundation"),
        org_email=os.getenv("ORG_EMAIL", "donations@openimpact.org"),
        org_website=os.getenv("ORG_WEBSITE", "https://openimpact.org"),
    )
    sent = mailer.send_refund_receipt(payload)
    console.print(f"[bold green]Donation {order_id} marked as REFUNDED.[/bold green]")
    console.print(f"Refund Notice sent to: [cyan]{donation.donor_email}[/cyan] ([dim]sent={sent}[/dim])")


@main.group(name="webhooks")
def webhooks_group() -> None:
    """Inspect and audit received PayPal webhooks."""
    pass


@webhooks_group.command(name="list")
@click.option("--limit", default=20, type=int, help="Maximum webhook events to display.")
@click.option("--db", default=None, help="Custom SQLite database path.")
def list_webhooks_cli(limit: int, db: Optional[str]) -> None:
    """Display recent webhook events received from PayPal."""
    repo = get_repo(db)
    events = repo.list_webhook_events(limit=limit)

    if not events:
        console.print("[yellow]No webhook events recorded in audit log.[/yellow]")
        return

    table = Table(title=f"PayPal Webhooks Audit Log (Latest {len(events)})", border_style="magenta")
    table.add_column("Event ID", style="cyan", no_wrap=True)
    table.add_column("Date (UTC)", style="dim")
    table.add_column("Event Type", style="bold yellow")
    table.add_column("Resource ID", style="green")
    table.add_column("Status", style="bold")

    for ev in events:
        table.add_row(
            ev.event_id,
            ev.created_at.strftime("%Y-%m-%d %H:%M:%S"),
            ev.event_type,
            ev.resource_id,
            f"[green]{ev.status}[/green]",
        )

    console.print(table)


if __name__ == "__main__":
    main()
