from __future__ import annotations

from app.features.document_intelligence.canonical.enums import (
    ExtractionDecision,
)
from app.features.document_intelligence.extraction.schemas import (
    ExtractionQuality,
    ExtractionPolicyResult,
)


class ExtractionPolicy:
    """Decides whether an extraction result is sufficient.

    The policy is intentionally backend-agnostic.

    It does not know whether a document was extracted by PyMuPDF,
    Docling, or another backend. It only evaluates extraction quality
    and determines whether the result can be accepted or whether a
    richer extraction backend should be attempted.
    """

    # Minimum quality requirements for accepting an extraction result.
    MIN_TEXT_SCORE = 0.75
    MIN_PROVENANCE_SCORE = 0.80

    # These are not hard failure thresholds. They are used to detect
    # severely degraded extraction results.
    MIN_OVERALL_SCORE = 0.60

    def evaluate(
        self,
        quality: ExtractionQuality,
    ) -> ExtractionPolicyResult:
        """Evaluate whether an extraction result should be accepted."""

        reasons: list[str] = []

        # ------------------------------------------------------------
        # Critical: text quality
        # ------------------------------------------------------------

        if quality.text_score < self.MIN_TEXT_SCORE:
            reasons.append(
                (
                    "Text extraction quality is below the minimum "
                    f"threshold ({quality.text_score:.2f} < "
                    f"{self.MIN_TEXT_SCORE:.2f})."
                )
            )

        # ------------------------------------------------------------
        # Critical: provenance
        # ------------------------------------------------------------

        if quality.provenance_score < self.MIN_PROVENANCE_SCORE:
            reasons.append(
                (
                    "Source provenance coverage is below the minimum "
                    f"threshold ({quality.provenance_score:.2f} < "
                    f"{self.MIN_PROVENANCE_SCORE:.2f})."
                )
            )

        # ------------------------------------------------------------
        # Overall quality
        # ------------------------------------------------------------

        if quality.overall_score < self.MIN_OVERALL_SCORE:
            reasons.append(
                (
                    "Overall extraction quality is below the minimum "
                    f"threshold ({quality.overall_score:.2f} < "
                    f"{self.MIN_OVERALL_SCORE:.2f})."
                )
            )

        # ------------------------------------------------------------
        # Decision
        # ------------------------------------------------------------

        if reasons:
            return ExtractionPolicyResult(
                decision=ExtractionDecision.FALLBACK,
                reasons=reasons,
            )

        return ExtractionPolicyResult(
            decision=ExtractionDecision.ACCEPT,
            reasons=[
                (
                    "Text extraction quality meets the minimum "
                    "requirements."
                ),
                (
                    "Source provenance coverage meets the minimum "
                    "requirements."
                ),
                (
                    "Overall extraction quality is sufficient for "
                    "the current extraction policy."
                ),
            ],
        )