from __future__ import annotations

import re

from app.features.document_intelligence.canonical.enums import BlockType
from app.features.document_intelligence.canonical.schemas import (
    CanonicalDocument,
)
from app.features.document_intelligence.extraction.schemas import (
    ExtractionQuality,
)


class ExtractionQualityEvaluator:
    """Evaluate the usefulness of an extracted document.

    This evaluator is backend-agnostic. It operates only on the canonical
    representation, so routing decisions don't become coupled to a specific
    extraction library.
    """

    _SUSPICIOUS_PATTERNS = (
        re.compile(r"\b\w+percent\b", re.IGNORECASE),
        re.compile(r"\b\w+(?:the|and|or|of|to)\b", re.IGNORECASE),
    )

    def evaluate(
        self,
        document: CanonicalDocument,
    ) -> ExtractionQuality:
        text_score = self._text_score(document)
        structure_score = self._structure_score(document)
        table_score = self._table_score(document)
        provenance_score = self._provenance_score(document)

        overall_score = self._overall_score(
            text_score=text_score,
            structure_score=structure_score,
            table_score=table_score,
            provenance_score=provenance_score,
        )

        warnings = self._warnings(
            document=document,
            text_score=text_score,
            structure_score=structure_score,
            table_score=table_score,
            provenance_score=provenance_score,
        )

        return ExtractionQuality(
            overall_score=round(overall_score, 3),
            text_score=round(text_score, 3),
            structure_score=round(structure_score, 3),
            table_score=round(table_score, 3),
            provenance_score=round(provenance_score, 3),
            warnings=warnings,
        )

    def _text_score(self, document: CanonicalDocument) -> float:
        """Score whether useful textual content was extracted."""

        if not document.blocks:
            return 0.0

        text_blocks = [
            block
            for block in document.blocks
            if block.text and block.text.strip()
        ]

        if not text_blocks:
            return 0.0

        non_empty_ratio = len(text_blocks) / len(document.blocks)

        total_chars = sum(
            len(block.text.strip())
            for block in text_blocks
        )

        # A document with only a handful of characters is suspicious.
        if total_chars == 0:
            return 0.0

        if total_chars < 50:
            content_score = 0.4
        elif total_chars < 200:
            content_score = 0.7
        else:
            content_score = 1.0

        suspicious_count = sum(
            self._has_suspicious_text(block.text or "")
            for block in text_blocks
        )

        suspicious_ratio = suspicious_count / len(text_blocks)

        artifact_score = max(
            0.0,
            1.0 - min(suspicious_ratio * 2.0, 1.0),
        )

        return (
            non_empty_ratio * 0.4
            + content_score * 0.4
            + artifact_score * 0.2
        )

    def _structure_score(self, document: CanonicalDocument) -> float:
        """Score how much useful document structure survived extraction."""

        if not document.blocks:
            return 0.0

        score = 0.0

        headings = [
            block
            for block in document.blocks
            if block.type in {
                BlockType.TITLE,
                BlockType.HEADING,
            }
        ]

        paragraphs = [
            block
            for block in document.blocks
            if block.type == BlockType.PARAGRAPH
        ]

        lists = [
            block
            for block in document.blocks
            if block.type == BlockType.LIST_ITEM
        ]

        # Text-only documents are not inherently low quality.
        # Give credit when we at least have multiple meaningful blocks.
        if len(document.blocks) >= 2:
            score += 0.35

        if headings:
            score += 0.30

        if paragraphs:
            score += 0.20

        if lists:
            score += 0.15

        return min(score, 1.0)

    def _table_score(self, document: CanonicalDocument) -> float:
        """Score structurally extracted tables.

        This intentionally does not penalize a document heavily just because
        it has no tables. A table score of 0 means "no structured tables
        available", not necessarily "bad extraction".
        """

        table_blocks = [
            block
            for block in document.blocks
            if block.type == BlockType.TABLE
        ]

        if not table_blocks:
            return 0.0

        valid_tables = [
            block
            for block in table_blocks
            if block.table is not None
            and block.table.rows > 0
            and block.table.columns > 0
            and block.table.cells
        ]

        return len(valid_tables) / len(table_blocks)

    def _provenance_score(self, document: CanonicalDocument) -> float:
        """Score whether extracted content can be traced back to the source."""

        if not document.blocks:
            return 0.0

        with_provenance = sum(
            bool(block.provenance)
            for block in document.blocks
        )

        return with_provenance / len(document.blocks)

    def _overall_score(
        self,
        *,
        text_score: float,
        structure_score: float,
        table_score: float,
        provenance_score: float,
    ) -> float:
        # Text and provenance matter most for the cheap native path.
        #
        # Tables deliberately receive a smaller weight because most documents
        # do not contain tables.
        return (
            text_score * 0.50
            + structure_score * 0.20
            + table_score * 0.10
            + provenance_score * 0.20
        )

    def _warnings(
        self,
        *,
        document: CanonicalDocument,
        text_score: float,
        structure_score: float,
        table_score: float,
        provenance_score: float,
    ) -> list[str]:
        warnings: list[str] = []

        if not document.pages:
            warnings.append("No pages were extracted.")

        if not document.blocks:
            warnings.append("No document blocks were extracted.")

        if text_score < 0.70:
            warnings.append("Text extraction quality is low.")

        if structure_score < 0.40:
            warnings.append("Limited document structure was extracted.")

        if (
            self._contains_table_like_content(document)
            and table_score == 0.0
        ):
            warnings.append(
                "Possible table content detected without structured table extraction."
            )

        if provenance_score < 0.90:
            warnings.append(
                "Some extracted blocks are missing source provenance."
            )

        if self._contains_suspicious_text(document):
            warnings.append(
                "Possible text extraction artifacts detected."
            )

        return warnings

    def _contains_table_like_content(
        self,
        document: CanonicalDocument,
    ) -> bool:
        """Detect likely tabular text without claiming it is a table."""

        for block in document.blocks:
            text = (block.text or "").strip()

            if not text:
                continue

            # Multiple whitespace-separated columns combined into a block
            # are a useful weak signal.
            tokens = text.split()

            if len(tokens) >= 4:
                numeric_tokens = sum(
                    self._looks_numeric(token)
                    for token in tokens
                )

                if numeric_tokens >= 2:
                    return True

        return False

    def _contains_suspicious_text(
        self,
        document: CanonicalDocument,
    ) -> bool:
        return any(
            self._has_suspicious_text(block.text or "")
            for block in document.blocks
        )

    def _has_suspicious_text(self, text: str) -> bool:
        return any(
            pattern.search(text)
            for pattern in self._SUSPICIOUS_PATTERNS
        )

    @staticmethod
    def _looks_numeric(token: str) -> bool:
        token = token.strip(".,:;()[]{}")

        return bool(
            re.fullmatch(
                r"(?:\d+(?:\.\d+)?%?)|(?:\d{1,3}(?:,\d{3})+)",
                token,
            )
        )