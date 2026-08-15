"""Buyer-protection due-diligence checklist generator.

Static legal checklist for Telegram alerts: Nota Simple check,
community zero-debt certificate, independent notary right, off-plan
bank guarantee, and the plusvalia clause abuse warning. Spanish copy is
user-facing Telegram content.
"""

from __future__ import annotations

from home_ops.scorer.models import ChecklistItem


class BuyerProtectionChecklist:
    """Generator of the five buyer-protection legal checklist items."""

    @staticmethod
    def items() -> list[ChecklistItem]:
        """Return the five fixed due-diligence items."""
        return [
            ChecklistItem(
                title="Nota Simple < 48 h",
                description=(
                    "Verifica cargas y embargos del Registro antes de firmar "
                    "arras o contrato (art. 1473 CC)."
                ),
            ),
            ChecklistItem(
                title="Certificado de deuda cero",
                description=(
                    "Pide al administrador certificado de que IBI y cuotas de "
                    "comunidad estan al dia: siguen a la propiedad."
                ),
            ),
            ChecklistItem(
                title="Notario independiente",
                description=(
                    "Tienes derecho a elegir notario propio (art. 1455 CC); "
                    "desconfia si te imponen uno."
                ),
            ),
            ChecklistItem(
                title="Garantia bancaria obra nueva",
                description=(
                    "Exige aval o seguro para entregas a cuenta en obra nueva "
                    "(Ley 38/1999)."
                ),
            ),
            ChecklistItem(
                title="Plusvalia municipal",
                description=(
                    "El impuesto es del vendedor (art. 1455 CC); rechaza "
                    "clausulas que lo trasladen al comprador."
                ),
            ),
        ]
