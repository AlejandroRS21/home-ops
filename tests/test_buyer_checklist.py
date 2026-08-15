"""Tests for BuyerProtectionChecklist — the 5 legal due-diligence items."""

from home_ops.scorer.checklist import BuyerProtectionChecklist
from home_ops.scorer.models import ChecklistItem


class TestBuyerProtectionChecklist:
    """Static legal checklist generation."""

    def test_five_items_generated(self) -> None:
        """GIVEN BuyerProtectionChecklist WHEN items THEN exactly 5 items."""
        items = BuyerProtectionChecklist.items()
        assert len(items) == 5

    def test_all_items_are_checklist_items(self) -> None:
        """GIVEN items WHEN generated THEN every item is a ChecklistItem."""
        for item in BuyerProtectionChecklist.items():
            assert isinstance(item, ChecklistItem)

    def test_all_items_have_title_and_description(self) -> None:
        """GIVEN items WHEN generated THEN title and description non-empty."""
        for item in BuyerProtectionChecklist.items():
            assert item.title, "title must not be empty"
            assert item.description, "description must not be empty"

    def test_titles_are_unique(self) -> None:
        """GIVEN items WHEN generated THEN no duplicate titles."""
        items = BuyerProtectionChecklist.items()
        assert len({i.title for i in items}) == len(items)

    def test_nota_simple_item_present(self) -> None:
        """GIVEN checklist THEN includes the Nota Simple <48h check."""
        texts = " ".join(i.title for i in BuyerProtectionChecklist.items())
        assert "Nota Simple" in texts

    def test_community_zero_debt_item_present(self) -> None:
        """GIVEN checklist THEN includes community zero-debt certificate."""
        texts = " ".join(
            f"{i.title} {i.description}" for i in BuyerProtectionChecklist.items()
        )
        assert "deuda cero" in texts.lower()

    def test_independent_notary_item_present(self) -> None:
        """GIVEN checklist THEN includes independent notary right (Art. 1455 CC)."""
        texts = " ".join(
            f"{i.title} {i.description}" for i in BuyerProtectionChecklist.items()
        )
        assert "notario" in texts.lower()
        assert "1455" in texts

    def test_off_plan_guarantee_item_present(self) -> None:
        """GIVEN checklist THEN includes off-plan bank guarantee (Ley 38/1999)."""
        texts = " ".join(
            f"{i.title} {i.description}" for i in BuyerProtectionChecklist.items()
        )
        assert "38/1999" in texts

    def test_plusvalia_clause_warning_present(self) -> None:
        """GIVEN checklist THEN includes plusvalia clause abuse warning."""
        texts = " ".join(
            f"{i.title} {i.description}" for i in BuyerProtectionChecklist.items()
        )
        assert "plusval" in texts.lower()
