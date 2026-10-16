"""Google Drive search settings.

Secrets are not kept here. The OAuth client secret and the keys that encrypt
stored Google tokens come from .env (GDRIVE_OAUTH_CLIENT_SECRET and
GDRIVE_TOKEN_ENCRYPTION_KEYS); see app.features.gdrive_search.connection.

The search thresholds were tuned together on a 290-question evaluation over a
synthetic Drive. Change them only alongside a re-run of that evaluation.
"""

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class GdriveSearchConfig:
    # Where Drive connections (encrypted refresh tokens) live. Only "redis"
    # exists today; see connection.build_connection_store.
    connection_store: str = "redis"

    # ---- Google OAuth ----
    # The OAuth client ("Web application") from Google Cloud. Not a secret:
    # it appears in the sign-in URL.
    oauth_client_id: str = "421837233831-t8qd6kngvhsm4tj7ikk5o3utvate0t8h.apps.googleusercontent.com"
    # Must match an authorised redirect URI on that OAuth client exactly.
    oauth_redirect_uri: str = "http://localhost:5006/api/v1/gdrive/oauth/callback"
    # Where the browser is sent once sign-in ends, with ?connected=1 or
    # ?error=<code>&message=<text> appended.
    frontend_redirect_url: str = "http://localhost:8501/"
    oauth_state_ttl_seconds: int = 600
    # Company email domains. A Google account outside them, or one their
    # Workspace does not manage, is reported as personal (a warning, not a
    # block). Empty: nothing is reported.
    company_email_domains: tuple[str, ...] = ()

    # ---- Drive API ----
    # Without this, files in Shared Drives are invisible to every search.
    include_shared_drives: bool = True
    # Drive calls in flight at once per worker process, across all requests:
    # one search alone reaches about 30 (searches, prefetches, the pool).
    # Downloads have slots of their own (see GoogleDriveClient).
    drive_api_concurrency: int = 48
    drive_download_concurrency: int = 48
    # A listing page takes about a second; this only bounds a stalled call.
    drive_request_timeout_seconds: float = 10.0
    # A call Google has not started answering by now is sent again and the
    # first answer used: about 1.5% of calls stall 3-30s at random, and a
    # quarter of questions hit one. Costs about 4% more calls. None: off.
    drive_hedge_after_seconds: Optional[float] = 2.0

    # ---- models ----
    # Reasoning is latency before the first output token; these are short
    # judgements with the rubric in the prompt.
    model_reasoning_effort: str = "none"
    # OpenAI's processing tier: None is the standard tier; "fast" (formerly
    # "priority") is about 0.3s faster a call, 0.85s a question with its
    # answer, at twice the token price. Billing charges the tier OpenAI
    # reports serving, which can fall back to standard.
    model_service_tier: Optional[str] = "fast"
    # Per attempt. The SDK retries a timed-out call.
    model_timeout_seconds: float = 12.0
    # A reply that cannot be read is asked for again, up to this many calls
    # in all, before the search fails (chat summary retries a batch the
    # same way).
    model_attempts: int = 2

    # ---- discovery ----
    # Results per Drive query (Drive's own cap is 1000).
    discovery_max_candidates: int = 60
    # Survivors of local ranking passed on to be judged.
    discovery_shortlist: int = 15
    # Documents returned for a browse, which has no subject to rerank on.
    discovery_max_files: int = 8
    # Files read for a browse by a named person ("files Priya shared with
    # me"): Drive cannot filter by a name, so theirs are picked out locally.
    discovery_person_browse_files: int = 300
    # Shortlist slots held for files Drive matched on content. 0 turns it off.
    discovery_content_slots: int = 5
    # Word pairs searched per term group of three or more words that matched
    # nothing, so one absent word does not cost the other three.
    discovery_relaxed_pairs: int = 6
    # Term groups from query understanding, each its own Drive query.
    discovery_max_queries: int = 4
    # Stricter than name_fuzzy_cutoff: incidental words from a sentence are
    # compared against every filename here.
    discovery_name_fuzzy_cutoff: float = 0.75

    # ---- verbatim search: the typed words, searched exactly ----
    literal_search_enabled: bool = True
    literal_max_results: int = 8
    # Enough to beat one uncorroborated expansion hit without outranking
    # several agreeing ones.
    literal_prefilter_boost: float = 6.0
    # Words ANDed into the verbatim query.
    literal_max_terms: int = 4
    # Also search the typed words query understanding names as the subject.
    literal_keywords: bool = True

    # ---- folder paths ----
    # Match the question's words against every file's folder path, locally.
    path_match_enabled: bool = True
    # A typed word is specific when at most this share of the Drive's files
    # carry it in their path (never a limit below rerank_pool).
    specific_word_max_share: float = 0.005
    path_match_max_files: int = 8

    # ---- reranking: read the candidates, then order them ----
    rerank_pool: int = 20
    rerank_max_results: int = 5
    # Pinned to the rerank rubric: 4-6 is "related and partially useful".
    rerank_min_score: float = 4.0
    # When set, the reranker replies with only the files scoring at least
    # this, the rest counting 0: a few lines where every file took twenty,
    # 0.18s faster a question at the same accuracy (two full evaluations).
    # Never above disambiguation_min_score, which near misses start at.
    # None: every file scored.
    rerank_list_min_score: Optional[float] = 3.0
    rerank_excerpt_chars: int = 1000
    # Larger files are judged on name and type instead of being downloaded.
    rerank_max_download_bytes: int = 10 * 1024 * 1024
    # Budget for downloading the whole pool; a file not in by then is
    # judged on metadata.
    rerank_fetch_timeout_seconds: float = 8.0

    # ---- answering: the question answered from the documents found ----
    answer_max_files: int = 5
    # A longer file is cut to its passage richest in the question's words.
    answer_file_chars: int = 8000
    # Across all files, best first: a fifth file gets what the first four left.
    answer_total_chars: int = 30000
    # The reranker has normally cached every document's text by now; this
    # covers a download still running from its deadline.
    answer_fetch_timeout_seconds: float = 3.0
    # Scans and photos have no text to extract: up to this many are sent to
    # the model as the file itself (a one-page scan is about 3,400 tokens).
    answer_native_max_files: int = 4
    answer_native_max_bytes: int = 10 * 1024 * 1024
    # All of them together, as sent: OpenAI takes about 32 MB of files a request.
    answer_native_total_bytes: int = 20 * 1024 * 1024
    # A scan's first pages only: every page costs tokens and time, and
    # OpenAI reads at most 100 a request.
    answer_native_max_pages: int = 20
    # A file Google is slow to hand over is left out rather than waited for.
    answer_native_timeout_seconds: float = 4.0
    # Downloaded ahead, while the reranker's call runs: the answer reads at
    # most answer_native_max_files plus answer_ocr_max_files of them.
    answer_native_prefetch_max: int = 6
    # Scans and photos Drive's search matched inside, which the reranker,
    # seeing only their names, did not keep: up to this many also go to the
    # answer as the files themselves, and it decides which file answers.
    answer_ocr_max_files: int = 2
    # When the answer reads the reranker's files and says they do not hold
    # what was asked, offer them to pick from instead of as results. Off:
    # measured, it passed every negative but demoted right files whose
    # answer came with a caveat ("these are last year's rates"), more
    # than it saved.
    answer_found_demotes: bool = False

    # ---- noise: operator exclusions, empty on purpose ----
    exclude_path_patterns: tuple[str, ...] = ()
    exclude_extensions: tuple[str, ...] = ()
    # A filename in at least this many folders is treated as generated and
    # demoted, never removed.
    template_name_min_folders: int = 4
    template_name_penalty: float = 4.0
    min_useful_file_bytes: int = 256

    # ---- confidence ----
    # Pinned to the rerank rubric, where 7-8 means "contains substantial
    # information needed to answer it".
    confidence_min_score: float = 7.0
    # Top-1 must lead top-2 by this much to be acted on silently.
    confidence_min_gap: float = 1.5
    # Topical questions relax that gap by this factor.
    confidence_topical_gap_factor: float = 0.4
    disambiguation_max_options: int = 5
    # Below this, "nothing matches" is the honest answer (rubric: 1-3).
    disambiguation_min_score: float = 3.0

    # ---- filename resolution ----
    # The whole Drive's names are cached so fuzzy matching is possible at
    # all. Past the TTL the old listing is served while a rebuild runs in
    # the background; past the max age it is rebuilt while the caller waits.
    name_cache_ttl_seconds: int = 900
    name_cache_max_age_seconds: int = 3600
    # With no listing cached, a question waits for the whole of a small
    # Drive's listing. A Drive with more files than this is large: Drive
    # returns 460 files a request, one request after another, so the question
    # goes ahead with the newest files listed so far while the build
    # finishes in the background for the next one.
    name_listing_large_files: int = 1000
    name_fuzzy_cutoff: float = 0.6
    name_fuzzy_max_matches: int = 5
    # A best match this far ahead of the runner-up is taken as the answer.
    name_fuzzy_decisive_gap: float = 0.15
    # Names shown to the model when edit distance finds nothing.
    name_llm_candidates: int = 50

    # ---- fetching file text ----
    # A download takes about a second whatever its size, so the reranker's
    # whole pool is read at once: at 6, twenty files took four waves.
    fetch_max_workers: int = 20
    fetch_cache_ttl_seconds: int = 3600
    fetch_max_chars_per_file: int = 200_000
    # Google refuses to export Docs above 10 MB.
    max_export_bytes: int = 10 * 1024 * 1024
    max_download_bytes: int = 50 * 1024 * 1024


gdrive_config = GdriveSearchConfig()
