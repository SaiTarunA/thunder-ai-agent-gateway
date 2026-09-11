from app.features.document_intelligence.canonical.enums import (
    ExtractionDecision,
)
from app.features.document_intelligence.extraction.policy import (
    ExtractionQuality,
    ExtractionPolicy,
)


def test_policy_accepts_good_extraction():
    policy = ExtractionPolicy()

    quality = ExtractionQuality(
        overall_score=0.76,
        text_score=0.90,
        structure_score=0.55,
        table_score=0.0,
        provenance_score=1.0,
        warnings=[
            (
                "Possible table content detected without structured "
                "table extraction."
            ),
            "Possible text extraction artifacts detected.",
        ],
    )

    result = policy.evaluate(quality)

    assert result.decision == ExtractionDecision.ACCEPT

    assert result.reasons


def test_policy_falls_back_when_text_quality_is_low():
    policy = ExtractionPolicy()

    quality = ExtractionQuality(
        overall_score=0.50,
        text_score=0.40,
        structure_score=0.80,
        table_score=0.0,
        provenance_score=1.0,
        warnings=[],
    )

    result = policy.evaluate(quality)

    assert result.decision == ExtractionDecision.FALLBACK

    assert any(
        "Text extraction quality" in reason
        for reason in result.reasons
    )


def test_policy_falls_back_when_provenance_is_low():
    policy = ExtractionPolicy()

    quality = ExtractionQuality(
        overall_score=0.70,
        text_score=0.90,
        structure_score=0.80,
        table_score=0.0,
        provenance_score=0.50,
        warnings=[],
    )

    result = policy.evaluate(quality)

    assert result.decision == ExtractionDecision.FALLBACK

    assert any(
        "provenance" in reason.lower()
        for reason in result.reasons
    )


def test_policy_falls_back_when_overall_quality_is_low():
    policy = ExtractionPolicy()

    quality = ExtractionQuality(
        overall_score=0.40,
        text_score=0.80,
        structure_score=0.10,
        table_score=0.0,
        provenance_score=0.90,
        warnings=[],
    )

    result = policy.evaluate(quality)

    assert result.decision == ExtractionDecision.FALLBACK