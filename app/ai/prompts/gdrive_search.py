"""Google Drive search prompts and model call configuration.

A change to the wording of the four model stages (understand, match
filename, rerank, answer) should be re-measured on the 290-question
evaluation, which scores answers against the facts they must contain. The
structured-reply schemas are the Pydantic models in
`app.features.gdrive_search.schemas`, next to the code that parses them.

The routing section and the context line are added to the intent router's
instructions unless the request leaves Drive out
(`AIConfigBuilder.prepare_intent_detection_config`).
"""

from app.ai.ai_constants import (
    FUNCTION_DRIVE_SEARCH,
    FUNCTION_GENERAL_QUERY,
    FUNCTION_INTENT_SEARCH,
)

# ---------------------------------------------------------------------
# Query understanding
# ---------------------------------------------------------------------

GDRIVE_UNDERSTAND_CONSTANTS = {
    "instructions": """\
You turn a person's question about their Google Drive into search
terms for Drive's own index.

Drive matches literal words only — no synonyms, no semantic
understanding — so the words someone types often miss the words their
documents contain. Closing that gap is the main thing you do.

You are given a sample of THIS person's own folder names and the words
that actually occur in their filenames. Treat it as your primary
evidence about what exists and what it is called here. Categories of
document are named differently by different people, in different
countries, in different jobs — so infer the vocabulary from what you
are shown rather than from what such documents are usually called.
Where the sample offers a word close to the question's meaning, prefer
it over a word you supplied yourself.

The sample is a subset, not the whole Drive. When nothing in it fits,
fall back to the plain words of the question plus ordinary
alternatives for them.

Produce:

term_groups
  Each group is a set of words ANDed together to form ONE search. Give
  2-4 groups, ordered most-specific first; they run in parallel and
  their results are pooled.
  - Some questions have NO subject at all — "everything shared with me
    last month", "what did I open yesterday", "my biggest
    spreadsheets". These are pure browsing: the narrowing fields below
    carry the whole question. Return an EMPTY list for term_groups and
    set those fields. Never put words like "shared", "last", "month",
    "recent" or "opened" into a group — they describe the filter, not
    the contents, and searching for them finds documents that happen
    to contain the word "shared".
  - 1-2 precise content words per group, never question words,
    pronouns or filler. Every word must appear, so each extra one is
    another way to find nothing.
  - At least one group from the vocabulary sample when it has anything
    relevant, and at least one of plain alternatives to the question's
    own wording.
  - A question may name a KIND of document rather than its contents
    ("identity papers", "tax stuff"). Such documents rarely contain
    their category name. Look for what the sample suggests this
    person's examples of that kind are actually called, and search for
    those instead.

intent
  entity  names a specific document ("my resume", "the Q4 deck").
          Ambiguity here is a problem: there is one right file.
  topic   asks about a subject. Several good sources are a fine
          answer, not a failure.
  corpus  spans the whole Drive ("summarise everything about X").

The remaining fields NARROW the search. They are powerful and
dangerous in the same way: a filter the question did not actually ask
for hides the right file completely, and the result looks like it
simply does not exist. Set each one ONLY when the question genuinely
implies it. "any", "" and false are the right answers most of the
time, an unset field is never a mistake, a wrong one usually is.

file_type
  Only when the question clearly implies one, else "any".

date_from / date_to
  ISO dates (YYYY-MM-DD), bounding when the file was last touched.
  Set from explicit or relative time references only — "last week",
  "in March", "since the summer", "2024". Leave both empty when the
  question says nothing about time. A question mentioning a date
  IN ITS SUBJECT ("the Q4 revenue report") is about content, not
  about when the file was edited — leave these empty for that.

date_field
  Which timestamp the range applies to.
  modified  the default — when the file last changed
  created   the question is about when it was written or received
  viewed    the question is about when THEY last looked at it
            ("the doc I opened yesterday")

ownership
  any     the default
  mine    the person's own files ("my notes", "the doc I wrote")
  shared  files other people shared with them ("the deck Priya sent
          me", "what's been shared with me")

person
  A name or email ONLY when the question says who owns, wrote, shared
  or sent the file ("the deck Sara sent me", "files Sam shared").
  A person the file is ABOUT, or addressed to, is its subject, not its
  owner: "John's resume" and "the letter for John's visa" leave this
  empty and put the name in term_groups instead.

folder
  A folder name if the question names a place ("in my taxes folder").
  Not a topic — only an actual location.

starred
  True only for an explicit reference to starred or flagged files.

order
  A superlative, which is an ordering rather than a filter.
  newest   "the latest", "most recent"
  oldest   "the first", "the original"
  largest  "the biggest"
  Use "none" otherwise. Do NOT also set a date range for these:
  "my latest invoice" means sort by date, not restrict to recent ones.
{keywords}""",
    # Spliced in at "{keywords}" only when the verbatim pass uses them
    # (gdrive_config.literal_keywords). It sits last so that the prompt
    # before it, the cached prefix, is the same with or without it.
    "keywords_instructions": """
keywords
  Not a narrowing field. The words of the question that name what is
  being looked for, copied exactly as typed: same spelling, same
  language, no synonyms, no corrections, no translation. They are
  searched literally beside term_groups, to catch a file that uses the
  person's own word where your groups chose another. Single words, at
  most 4, most specific first. Leave out greetings, requests,
  politeness and filler ("hi", "can you", "I'm looking for",
  "thanks"), and words that only describe a filter ("latest",
  "shared", "last month"). Empty for a pure browse.
""",
    "tool_description": "Submit the search plan for the question.",
    "temperature": 0.0,
    "max_response_output_tokens": 1000,
}

# ---------------------------------------------------------------------
# Semantic filename matching
# ---------------------------------------------------------------------

GDRIVE_MATCH_FILENAME_CONSTANTS = {
    "instructions": """\
A person typed a filename from memory. Identify which of the listed
files they meant.

What they typed may not resemble any filename literally. Handle:
- synonyms: "my CV" for a file named Resume, "deck" for a presentation
- descriptions: "the excel one", "the bank thing"
- half-remembered or reordered titles
- a name so misremembered that only the topic survives

Return every file that is a genuine possibility, best first — usually
one to three. Return an empty list if nothing plausibly matches;
guessing wrongly is worse than admitting there is no match, because
the caller can then ask the person directly.
""",
    "tool_description": "Submit the files the typed name could mean, best first; an empty list if none.",
    "temperature": 0.0,
    "max_response_output_tokens": 500,
}

# ---------------------------------------------------------------------
# Content reranking
# ---------------------------------------------------------------------

GDRIVE_RERANK_CONSTANTS = {
    "instructions": """\
You are the reranking stage of a search over one person's Google Drive.

Below is their question, then numbered candidate files. Each shows the
file's metadata and a PASSAGE: the part of the file where the
question's words appear most, not necessarily its beginning. Some files
have no readable text and show metadata only.

Score each file 0-10 for how useful it is for answering THIS question:

  9-10  Directly answers it, or is exactly the document asked for.
  7-8   Contains substantial information needed to answer it.
  4-6   Related and partially useful; touches the topic without
        resolving the question.
  1-3   Same general area, but does not help answer it.
  0     Unrelated.

Rules:
- Judge usefulness, not word overlap. A passage that repeats the
  question's words and resolves nothing scores low.
- A file with metadata only is judged on its name, folder and type,
  and is not penalised for having no text shown.
- Some files with no readable text list the words Drive's own search
  found inside them: Drive reads the text in images and scans. Treat
  those words as the file's content, and judge whether a file holding
  them answers the question.
- Be decisive and spread your scores; most candidate sets hold only a
  few genuinely useful files.
- Reply in CSV and nothing else: the header line "index,score", then
  one line per file, every index once:
  index,score
  0,7
  1,0
""",
    "temperature": 0.0,
    # The reply is one short line per file, so its cap is computed per call
    # (output_tokens_per_line per line, never under the API minimum): a reply
    # wandering into prose is cut off rather than allowed to hold up the
    # search. A line is about 4 tokens, 6 with a decimal score; the spare
    # covers a code fence. The fixed cap covers a full pool.
    "output_tokens_per_line": 8,
    "max_response_output_tokens": 168,
}

# The same instructions with a shorter reply: only the files scoring at
# least {min_score}, a few lines where every file took twenty. Used when
# gdrive_config.rerank_list_min_score is set.
GDRIVE_RERANK_CONSTANTS["instructions_listed"] = GDRIVE_RERANK_CONSTANTS["instructions"].replace(
    """\
- Reply in CSV and nothing else: the header line "index,score", then
  one line per file, every index once:
  index,score
  0,7
  1,0
""",
    """\
- Reply in CSV and nothing else: the header line "index,score", then
  one line for each file scoring {min_score} or more. A file you leave
  out scores 0; with none, reply with the header alone:
  index,score
  3,8
  5,{min_score}
""",
)

# ---------------------------------------------------------------------
# Answering from the documents found
# ---------------------------------------------------------------------

GDRIVE_ANSWER_CONSTANTS = {
    "instructions": """\
You answer a person's question from files in their own Google Drive.

Below are the question and numbered files. Each shows the file's name,
folder, type and date, then its text, or the part of it where the
question's words appear most. A scan or a photo is shown as the file
itself; any other file with no readable text shows its details only.

Rules:
- Answer only from these files. When they do not hold the answer, say
  so plainly; never guess, and never fill a gap from general knowledge.
- Cite the file each fact comes from as [n], its number, right after
  the fact. A fact found in several files cites each: [1][3].
- A question that only asks for a file ("find my passport scan", or
  just a file's name) is answered by saying which file it is, with its
  citation, and in a sentence what it holds.
- A LISTING line means the files are a list the question asked for
  ("my starred files", "what's in the Compliance folder"). Answer from
  the list: its number of files for "how many" (a count needs no
  citation), the files by name for what they are. When not all of them
  are numbered, say how many there are in all and that these are the
  first. Files listed beyond those shown in full have their details
  only. Never mention the listing itself.
- Copy figures, amounts, dates and names exactly as the file gives
  them.
- When files disagree, or are versions of one document, say which says
  what, with their dates.
- When the question names a time ("this month", "last year") and the
  files are from another, give what they say with their dates rather
  than presenting it as the answer for that time.
- A file shown without text could not be read and is not evidence
  either way. Mention it only when the answer is likely inside it.
- Scans and photos are shown as the files themselves, some of them
  because Drive's search found the question's words inside. Weigh every
  file on what it says, whatever its number, and answer from the ones
  that hold the answer.
- Lead with the answer: no restating the question, no preamble. A few
  sentences at most, or a short list when there are several items.
- Reply in the language of the question.
- Set found to true when the files hold the answer, or are the file or
  list the question asks for; false when they do not, and the answer
  says so.
""",
    "tool_description": "Submit the answer to the person's question, and whether the files hold it.",
    "temperature": 0.0,
    # Inside a JSON reply, where running out means no answer at all: a
    # long listing, or a reply in Hindi, takes more tokens than it looks.
    "max_response_output_tokens": 1500,
}

# ---------------------------------------------------------------------
# Intent routing (added unless the request leaves Drive out)
# ---------------------------------------------------------------------

DRIVE_CONTEXT_LINE_TEMPLATE = "- google_drive_connected: {connected}\n"

DRIVE_SEARCH_ROUTING_INSTRUCTIONS = f"""
=====================================================================
8. GOOGLE DRIVE
=====================================================================
{FUNCTION_DRIVE_SEARCH} is also an intent function, and its source_question is the one other field you write text in. It finds files in the user's own connected Google Drive. Files and documents shared in conversations are not in Google Drive: finding those stays with {FUNCTION_INTENT_SEARCH}. CURRENT CONTEXT says whether a Drive is connected (google_drive_connected).
Apply these rules before STEP 5:
  a. The user wants to find, locate, or open one of their files, or asks which file holds something, and refers to Google Drive ("in my drive", "on Google Drive", "my Drive folder", "the Google Doc in my drive") -> {FUNCTION_DRIVE_SEARCH} with confirm_source false, whether or not a Drive is connected. Naming Drive settles where to look, even when the request also mentions messages.
  b. The user wants to find or search for a file or document of any kind (a spreadsheet, invoice, contract, scan, photo, notes), or items they starred, which can be in Drive or in chats, and names no place for it: no Drive, and no chat, channel, DM, or conversation it was shared or sent in:
       google_drive_connected true  -> {FUNCTION_DRIVE_SEARCH} with confirm_source true. The backend asks whether to search Drive or chats.
       google_drive_connected false -> STEP 5 as before.
  c. The file was shared or sent in a chat, channel, or conversation ("the invoice Priya shared in the finance channel", "files from my chat with Rahul"), or the user wants messages someone sent or what was said in a conversation ("messages from Rahul about the launch", "what did Anil say in the design channel") -> {FUNCTION_INTENT_SEARCH}, never {FUNCTION_DRIVE_SEARCH}.
  d. A follow-up answering a pending {FUNCTION_DRIVE_SEARCH} question that chooses Drive ("Drive", "yes, check my drive") -> {FUNCTION_DRIVE_SEARCH} with confirm_source false, for the original request. One that chooses chats -> {FUNCTION_INTENT_SEARCH} for the original request.
  e. Not {FUNCTION_DRIVE_SEARCH}: a question about a document available in context (STEP 3); uploading, sharing, renaming, moving, or deleting Drive files (STEP 2); how-to questions about Google Drive itself ({FUNCTION_GENERAL_QUERY}).
Parameters:
  - query: what the user is looking for, in their own words as typed (for a follow-up, the original request). Drop greetings and the instruction to search Drive; do not rewrite it into keywords, translate it, or add words.
  - confirm_source: true only in case b; otherwise false.
  - source_question: when confirm_source is true, one short question in the user's language asking whether to look in their Google Drive or in their chats; otherwise "".
Examples (google_drive_connected true unless stated):
"find my passport scan in google drive"
  -> {FUNCTION_DRIVE_SEARCH}: query "my passport scan", confirm_source false, source_question ""
"which doc in my drive has the roof repair quote for the Maple Street house?"
  -> {FUNCTION_DRIVE_SEARCH}: query "which doc has the roof repair quote for the Maple Street house?", confirm_source false, source_question ""
"where's the Q3 budget spreadsheet?"
  -> {FUNCTION_DRIVE_SEARCH}: query "the Q3 budget spreadsheet", confirm_source true, source_question "Should I look for the Q3 budget spreadsheet in your Google Drive or in your chats?"
"pull up the things I starred"
  -> {FUNCTION_DRIVE_SEARCH}: query "the things I starred", confirm_source true, source_question "Should I look for your starred items in your Google Drive or in your chats?"
"search for the signed rental agreement"
  -> {FUNCTION_DRIVE_SEARCH}: query "the signed rental agreement", confirm_source true, source_question "Should I look for the signed rental agreement in your Google Drive or in your chats?"
"look up the warranty card in my drive, and the messages about it"
  -> {FUNCTION_DRIVE_SEARCH}: query "the warranty card", confirm_source false, source_question ""
"where's the Q3 budget spreadsheet?" (google_drive_connected false)
  -> {FUNCTION_INTENT_SEARCH}: query "Q3 budget spreadsheet", content_types ["files", "documents"], channel_types ["dm", "private_channel"], after null, before null, include_context_messages false, cursor null, limit 20, sort "score", sort_direction "desc", modifiers null, retrieval_methods ["lexical"]
"find the invoice Priya shared in the finance channel"
  -> {FUNCTION_INTENT_SEARCH}: query "invoice Priya finance", content_types ["files", "documents"], channel_types ["dm", "private_channel", "public_channel"], after null, before null, include_context_messages false, cursor null, limit 20, sort "score", sort_direction "desc", modifiers null, retrieval_methods ["lexical"]
"how do I share a folder in Google Drive?"
  -> {FUNCTION_GENERAL_QUERY}: message with the steps
"""

# ---------------------------------------------------------------------
# User-facing replies
# ---------------------------------------------------------------------

DRIVE_SOURCE_QUESTION_FALLBACK = "Should I look in your Google Drive or in your chats?"
# No file clearly matched: the closest ones go back as options to pick from.
DRIVE_WHICH_FILE_QUESTION = "I'm not sure which file you mean. Is it one of these?"
DRIVE_NOT_FOUND_MESSAGE = (
    "I couldn't find that in your Drive. Can you tell me the file's name, the closest name you "
    "remember, or what it contained?"
)
# A listing ("what did I edit yesterday") that nothing matches.
DRIVE_NO_FILES_MESSAGE = "No files in your Drive match that."
# A request with nothing to search for or list by ("check my drive").
DRIVE_WHAT_TO_FIND_MESSAGE = "What should I look for in your Drive? Tell me a file's name, or what it's about."
DRIVE_UNAVAILABLE_MESSAGE = "Google Drive isn't responding right now. Please try again in a moment."
# Opens the answer's input when the files are a listing ("my starred files").
GDRIVE_LISTING_NOTE_TEMPLATE = (
    "LISTING: {matched} files in their Drive match what the question asks for; the numbered files "
    "below are the first {listed} of them, in the order the question asks for (newest first unless "
    "it asks otherwise), sorted across all of them: the first is the newest, biggest or oldest of "
    "every match."
)
DRIVE_CONNECT_REQUIRED_MESSAGE = "Your Google Drive isn't connected yet. Connect it, then ask again."
DRIVE_AUTH_REQUIRED_MESSAGE = (
    "Your Google Drive connection has expired or was revoked. Reconnect Google Drive, then ask again."
)
