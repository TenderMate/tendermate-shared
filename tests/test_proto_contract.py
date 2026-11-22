from __future__ import annotations

from tendermate_shared.grpc.tendermate.agent.v1 import AnalysisRecord, MatchResult, UnitConversion


def test_sale_terms_round_trip() -> None:
    sent = MatchResult(
        product_id="p1",
        etm_price_unit="м",
        etm_order_multiple=100.0,
        etm_unit_conversions=[UnitConversion(name="км", multiplier=1000.0, source="etm")],
        ai_analyses=[
            AnalysisRecord(
                candidate_url="u",
                price_unit="упак",
                order_multiple=1.0,
                unit_conversions=[UnitConversion(name="шт", multiplier=0.01, source="name")],
            )
        ],
    )
    got = MatchResult().parse(bytes(sent))
    assert (got.etm_price_unit, got.etm_order_multiple) == ("м", 100.0)
    assert got.etm_unit_conversions[0] == UnitConversion(name="км", multiplier=1000.0, source="etm")
    record = got.ai_analyses[0]
    assert (record.price_unit, record.order_multiple) == ("упак", 1.0)
    assert record.unit_conversions[0].multiplier == 0.01  # double: exact, unlike the float prices


def test_message_without_terms_reads_as_unknown() -> None:
    got = MatchResult().parse(bytes(MatchResult(product_id="p1", etm_price=1.5)))
    assert (got.etm_price_unit, got.etm_order_multiple, got.etm_unit_conversions) == ("", 0.0, [])
