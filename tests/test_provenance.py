import pytest

from sightline.provenance import Bullet, ProvenanceError, assert_shippable


def verified(ref: str, text: str, provenance: str) -> Bullet:
    return Bullet(ref=ref, text=text, provenance=provenance, status="verified")


def test_derived_never_ships():
    bullets = [verified("BL-001", "Built a system that saves time.", "derived")]
    with pytest.raises(ProvenanceError, match="derived claims never ship"):
        assert_shippable(bullets)


def test_modeled_without_qualifier_rejected():
    bullets = [verified("BL-002", "Replaced $9,500 per month of agency labor.", "modeled")]
    with pytest.raises(ProvenanceError, match="needs an explicit qualifier"):
        assert_shippable(bullets)


def test_modeled_with_qualifier_passes():
    bullets = [
        verified("BL-003", "Replaced an estimated $9,500 per month of agency labor.", "modeled")
    ]
    assert_shippable(bullets)  # should not raise


@pytest.mark.parametrize("qualifier", ["estimated", "modeled", "projected", "approximately"])
def test_each_qualifier_word_satisfies_modeled(qualifier: str):
    bullets = [verified("BL-004", f"Grew revenue, {qualifier} at $12M.", "modeled")]
    assert_shippable(bullets)  # should not raise


def test_unverified_status_rejected_even_with_good_provenance():
    bullets = [Bullet(ref="BL-005", text="Shipped 68 API endpoints.", provenance="measured",
                       status="draft")]
    with pytest.raises(ProvenanceError, match="status=draft"):
        assert_shippable(bullets)


def test_stated_and_measured_pass_when_verified():
    bullets = [
        verified("BL-006", "Scaled a team to 25 employees.", "measured"),
        verified("BL-007", "Owned requirements and sprint planning.", "stated"),
    ]
    assert_shippable(bullets)  # should not raise


def test_stops_at_first_bad_bullet_in_a_mixed_batch():
    bullets = [
        verified("BL-008", "Scaled a team to 25 employees.", "measured"),
        verified("BL-009", "Built a system that was never shipped.", "derived"),
    ]
    with pytest.raises(ProvenanceError, match="BL-009"):
        assert_shippable(bullets)
