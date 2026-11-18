# Company policy documents, for questions like "How many days of annual leave do I get?".
#
# The policies are Markdown files in agent/policies/. The first line of each file says which
# roles may read it, for example:
#     visible_to_roles: hr, admin
# Each file is cut into chunks at every "## " heading, so a search returns a short section,
# not a whole file (the model can only read a little text at once).
#
# Policies EXPLAIN the rules, they never decide anything: what a user may do always comes live
# from the CRUD API (/me/permissions), and the CRUD API checks every action itself.
#
# How the search works today: look for the words of the question in each chunk. A rare word
# counts more than a common one: "approve" says more than "leave", which is in almost every chunk.
# No AI and no database: the docs are small, so that is enough. Later, find_policies() can be
# swapped for a vector search (pgvector) without changing the tool that calls it.

import re
from dataclasses import dataclass
from pathlib import Path

POLICY_FOLDER = Path(__file__).parent.parent / "policies"
ROLES = {"employee", "manager", "analyst", "hr", "admin"}
MAX_RESULTS = 2  # at most this many chunks go to the model

# Words that appear in almost every question, so they say nothing about which policy fits.
STOP_WORDS = {"the", "and", "for", "what", "how", "many", "much", "does", "can", "about", "with",
              "are", "you", "your", "our", "who", "when", "get", "there", "any", "have", "need",
              "must", "should", "may", "will",
              "policy", "policies", "rule", "rules", "company"}


@dataclass
class PolicyChunk:
    title: str                   # "Leave policy: Sick leave"
    text: str
    visible_to_roles: list[str]  # only these roles may read it


def load_policies(folder: Path = POLICY_FOLDER) -> list[PolicyChunk]:
    """Read every .md file in the folder and cut it into chunks, one per "## " section."""
    chunks: list[PolicyChunk] = []
    for path in sorted(folder.glob("*.md")):
        roles_line, rest = path.read_text(encoding="utf-8").split("\n", 1)
        if not roles_line.startswith("visible_to_roles:"):
            raise ValueError(f"{path.name} must start with a line like 'visible_to_roles: hr, admin'")
        roles = [role.strip() for role in roles_line.removeprefix("visible_to_roles:").split(",")]
        # A typo ("managr") would silently hide the file from that role, so refuse it.
        unknown = set(roles) - ROLES
        if unknown:
            raise ValueError(f"{path.name}: unknown roles {sorted(unknown)}")

        doc_title, *sections = rest.split("\n## ")
        doc_title = doc_title.strip().removeprefix("# ")
        for section in sections:
            heading, _, body = section.partition("\n")
            chunks.append(PolicyChunk(f"{doc_title}: {heading.strip()}", body.strip(), roles))
    return chunks


# Read once when the agent starts. After editing a policy file, restart the agent.
POLICIES = load_policies()


def readable_by(role: str) -> list[PolicyChunk]:
    """The chunks this role may read. Everything else is never even searched."""
    return [chunk for chunk in POLICIES if role in chunk.visible_to_roles]


def words_of(text: str) -> set[str]:
    """The useful words of a text, lowercase, without a plural "s": "Sick days?" -> {"sick", "day"}."""
    words = re.findall(r"[a-z]+", text.lower())
    return {word.removesuffix("s") for word in words if len(word) >= 3 and word not in STOP_WORDS}


def find_policies(question: str, role: str) -> list[PolicyChunk]:
    """The chunks that fit the question best, ONLY from the ones this role may read."""
    chunks = readable_by(role)  # the role filter comes FIRST
    chunk_words = [words_of(chunk.title + " " + chunk.text) for chunk in chunks]

    scored: list[tuple[float, PolicyChunk]] = []
    for chunk, words in zip(chunks, chunk_words):
        score = 0.0
        for word in words_of(question) & words:
            # A word that is in 1 chunk scores 1, a word that is in 4 chunks scores 1/4.
            in_how_many_chunks = sum(1 for other in chunk_words if word in other)
            score += 1 / in_how_many_chunks
        if score > 0:
            scored.append((score, chunk))
    scored.sort(key=lambda pair: pair[0], reverse=True)  # best first
    return [chunk for _, chunk in scored[:MAX_RESULTS]]
