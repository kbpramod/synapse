import fnmatch
import os
from typing import List, Dict, Any, Tuple


class DiffFilterService:
    """
    Filters and classifies PR diffs and changed files to optimize LLM cost,
    context window utilization, and changelog accuracy.
    """

    DEFAULT_IGNORED_FILES = {
        "package-lock.json",
        "yarn.lock",
        "pnpm-lock.yaml",
        "poetry.lock",
        "Cargo.lock",
        "Pipfile.lock",
        "composer.lock",
        "go.sum",
        ".DS_Store",
        "thumbs.db"
    }

    DEFAULT_IGNORED_DIRECTORIES = [
        "node_modules/*",
        "dist/*",
        "build/*",
        ".next/*",
        "coverage/*",
        "target/*",
        "vendor/*",
        "__pycache__/*",
        ".venv/*",
        ".git/*",
        ".turbo/*",
        "out/*",
        ".idea/*",
        ".vscode/*"
    ]

    DEFAULT_IGNORED_EXTENSIONS = {
        ".min.js",
        ".min.css",
        ".map",
        ".lock",
        ".pyc",
        ".class",
        ".jar",
        ".wasm",
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".svg",
        ".ico",
        ".woff",
        ".woff2",
        ".ttf",
        ".eot",
        ".mp4",
        ".webm",
        ".mp3",
        ".pdf",
        ".zip",
        ".tar",
        ".gz"
    }

    DEFAULT_LOW_VALUE_FILES = {
        ".gitignore",
        ".prettierignore",
        ".eslintignore",
        "LICENSE",
        "LICENSE.md",
        "LICENSE.txt"
    }

    def __init__(
        self,
        max_file_diff_chars: int = 4000,
        max_total_diff_chars: int = 20000
    ):
        self.max_file_diff_chars = max_file_diff_chars
        self.max_total_diff_chars = max_total_diff_chars
        self.ignored_files = set(self.DEFAULT_IGNORED_FILES)
        self.ignored_directories = list(self.DEFAULT_IGNORED_DIRECTORIES)
        self.ignored_extensions = set(self.DEFAULT_IGNORED_EXTENSIONS)
        self.low_value_files = set(self.DEFAULT_LOW_VALUE_FILES)

    def classify_file(self, filename: str) -> str:
        """
        Classifies a file into:
        - "IGNORED": Lockfiles, dependencies, generated directories, binaries (completely skip patch)
        - "LOW_VALUE": Config/license boilerplate (send metadata only)
        - "RELEVANT": Meaningful code/documentation changes (send full diff)
        """
        basename = os.path.basename(filename)
        _, ext = os.path.splitext(filename)

        # 1. Exact ignored files
        if basename in self.ignored_files:
            return "IGNORED"

        # 2. Ignored directory patterns
        normalized_filename = filename.replace("\\", "/")
        for pattern in self.ignored_directories:
            if fnmatch.fnmatch(normalized_filename, pattern) or fnmatch.fnmatch(basename, pattern):
                return "IGNORED"

        # 3. Ignored extensions
        if ext.lower() in self.ignored_extensions:
            return "IGNORED"

        # 4. Low value files
        if basename in self.low_value_files:
            return "LOW_VALUE"

        return "RELEVANT"

    def filter_and_format_diff(
        self,
        files_data: List[Dict[str, Any]]
    ) -> Tuple[str, List[str], Dict[str, int]]:
        """
        Takes raw list of changed file objects (from GitHub API or webhook payload):
        [{"filename": "src/cache.ts", "patch": "@@ -1,5 +1,10 @@...", "additions": 10, "deletions": 2}, ...]

        Returns:
        1. Formatted diff snippet string capped at max_total_diff_chars.
        2. Clean list of relevant changed file names.
        3. Statistics dictionary.
        """
        diff_chunks: List[str] = []
        relevant_filenames: List[str] = []
        current_total_chars = 0

        stats = {
            "total_files": len(files_data),
            "relevant_files": 0,
            "low_value_files": 0,
            "ignored_files": 0,
            "truncated": False
        }

        for f in files_data:
            filename = f.get("filename") or f.get("name") or "unknown"
            patch = f.get("patch") or ""
            classification = self.classify_file(filename)

            if classification == "IGNORED":
                stats["ignored_files"] += 1
                continue

            if classification == "LOW_VALUE":
                stats["low_value_files"] += 1
                relevant_filenames.append(filename)
                additions = f.get("additions", 0)
                deletions = f.get("deletions", 0)
                header = f"\n--- File: {filename} (Metadata only: +{additions}/-{deletions} lines) ---\n"
                if current_total_chars + len(header) <= self.max_total_diff_chars:
                    diff_chunks.append(header)
                    current_total_chars += len(header)
                continue

            # Classification == "RELEVANT"
            stats["relevant_files"] += 1
            relevant_filenames.append(filename)

            if patch:
                # Truncate individual file diff if it exceeds file threshold
                truncated_patch = patch[:self.max_file_diff_chars]
                if len(patch) > self.max_file_diff_chars:
                    truncated_patch += f"\n... [diff truncated for {filename}] ..."

                file_diff_block = f"\n--- File: {filename} ---\n{truncated_patch}\n"

                if current_total_chars + len(file_diff_block) <= self.max_total_diff_chars:
                    diff_chunks.append(file_diff_block)
                    current_total_chars += len(file_diff_block)
                else:
                    remaining_budget = self.max_total_diff_chars - current_total_chars
                    if remaining_budget > 100:
                        diff_chunks.append(file_diff_block[:remaining_budget] + "\n... [total diff budget reached] ...")
                    stats["truncated"] = True
                    break
            else:
                header = f"\n--- File: {filename} (Modified) ---\n"
                if current_total_chars + len(header) <= self.max_total_diff_chars:
                    diff_chunks.append(header)
                    current_total_chars += len(header)

        formatted_diff = "".join(diff_chunks)
        return formatted_diff, relevant_filenames, stats


diff_filter_service = DiffFilterService()
