from typing import Optional, List, Dict, Any
from src.ai.llm_service import llm_service
from src.ai.embedding_service import embedding_service


class ChangeAnalyzerService:

    async def analyze_pr(
        self,
        title: str,
        body: str,
        changed_files: Optional[List[str]] = None,
        diff_snippet: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Takes raw PR title, body, list of changed files, and diff patch.
        Uses LLM to construct a semantic ChangeRecord JSON and computes vector embeddings.
        """
        files = changed_files or []
        diff = diff_snippet or ""

        # 1. Generate semantic ChangeRecord via LLM
        change_record = await llm_service.analyze_pr_change(
            pr_title=title,
            pr_body=body,
            changed_files=files,
            diff_snippet=diff
        )

        # 2. Build canonical representation string for vector RAG indexing
        summary = change_record.get("summary", title)
        changes_str = "; ".join(change_record.get("changes", []))
        areas_str = ", ".join(change_record.get("areas", []))

        text_to_embed = f"PR Title: {title}. Summary: {summary}. Changes: {changes_str}. Areas: {areas_str}."

        # 3. Generate embedding vector
        try:
            embedding = await embedding_service.embed(text_to_embed)
        except Exception as e:
            print(f"[EMBEDDING ERROR] Failed to embed change record: {e}")
            embedding = None

        return {
            "summary": summary,
            "changes": change_record.get("changes", []),
            "areas": change_record.get("areas", []),
            "text_representation": text_to_embed,
            "embedding": embedding
        }


change_analyzer_service = ChangeAnalyzerService()
