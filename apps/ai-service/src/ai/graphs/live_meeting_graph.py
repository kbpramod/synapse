import json
import logging
import os
import re
from typing import TypedDict, List, Dict, Any, Optional

from langchain_core.messages import SystemMessage, HumanMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import StateGraph, END, START

from db.database import SessionLocal
from services.hybrid_search_service import hybrid_search_service

logger = logging.getLogger(__name__)


# =====================================================================
# 1. State Definition for Live Meeting Graph
# =====================================================================

class LiveMeetingGraphState(TypedDict, total=False):
    """
    State passed through the LangGraph live meeting processing pipeline.
    Maintains the 4 core context elements across 4-minute increments.
    """
    meeting_id: str
    organization_id: Optional[str]
    repository_id: Optional[str]
    window_index: int
    window_start_sec: Optional[float]
    window_end_sec: Optional[float]
    current_window_transcript: str
    current_window_segments: List[Dict[str, Any]]
    
    # 1. Previous Minutes Summary (Rolling executive context)
    previous_minutes_summary: str
    
    # 2. Conditional RAG Elements
    force_rag: bool
    rag_needed: bool
    rag_query: Optional[str]
    rag_context: List[Dict[str, Any]]
    
    # 3. Meeting State
    members: List[str]
    recent_decisions: List[Dict[str, Any]]
    tasks: List[Dict[str, Any]]
    
    # Newly extracted outputs from the current 4-minute window:
    extracted_members: List[str]
    new_decisions: List[Dict[str, Any]]
    new_tasks: List[Dict[str, Any]]
    window_summary: str
    accumulated_summary: str
    error: Optional[str]


# =====================================================================
# 2. LLM Factory
# =====================================================================

def get_chat_model(temperature: float = 0.1) -> ChatOpenAI:
    """Returns configured LangChain ChatOpenAI instance matching project settings."""
    api_key = os.getenv("AICREDITS_API_KEY") or os.getenv("OPENAI_API_KEY", "dummy-key")
    base_url = os.getenv("AICREDITS_BASE_URL", "https://api.aicredits.in/v1")
    model_name = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    return ChatOpenAI(
        model=model_name,
        api_key=api_key,
        base_url=base_url,
        temperature=temperature
    )


# =====================================================================
# 3. Graph Nodes
# =====================================================================

def assess_rag_necessity_node(state: LiveMeetingGraphState) -> Dict[str, Any]:
    """
    Node 1: Evaluates whether the current 4-minute window requires RAG retrieval.
    User rule: 'related things from rag database maybe but it is not necessary everytime'.
    
    Heuristics:
    - If force_rag is set -> True.
    - If window transcript is trivial / pleasantries -> False.
    - If transcript references past architectural decisions, schemas, PRs,
      APIs, repos, or engineering questions -> True and extracts query keywords.
    """
    transcript = (state.get("current_window_transcript") or "").strip()
    force_rag = state.get("force_rag", False)

    if force_rag:
        logger.info(f"[LIVE GRAPH] RAG forced for window {state.get('window_index', 1)}")
        return {
            "rag_needed": True,
            "rag_query": transcript[:200]
        }

    # If transcript is very short or missing, skip RAG
    if len(transcript) < 40:
        return {
            "rag_needed": False,
            "rag_query": None,
            "rag_context": []
        }

    # Look for technical references, questions about past decisions, architecture, repositories
    technical_patterns = [
        r"\b(architecture|database|schema|table|migration|postgres|redis|kafka|vector)\b",
        r"\b(endpoint|api|route|graphql|webhook|service|auth|jwt|clerk)\b",
        r"\b(pr|pull request|commit|repo|repository|branch|deploy|deployment)\b",
        r"\b(decision|decided|last week|previously|documented|specification|spec)\b",
        r"\b(how did we|why did we|where is the|do we already have|is there a)\b"
    ]
    
    has_match = any(re.search(pat, transcript, re.IGNORECASE) for pat in technical_patterns)
    has_question = "?" in transcript

    rag_needed = has_match or (has_question and len(transcript) > 100)

    if rag_needed:
        # Generate targeted query: extract sentences containing matches or questions
        sentences = [s.strip() for s in re.split(r'[.?!]\s+', transcript) if s.strip()]
        matched_sentences = [
            s for s in sentences
            if any(re.search(pat, s, re.IGNORECASE) for pat in technical_patterns) or "?" in s
        ]
        query_text = " ".join(matched_sentences[:2]) if matched_sentences else transcript[:200]
        logger.info(f"[LIVE GRAPH] Window {state.get('window_index', 1)} needs RAG. Query: '{query_text[:80]}...'")
        return {
            "rag_needed": True,
            "rag_query": query_text
        }

    logger.info(f"[LIVE GRAPH] Window {state.get('window_index', 1)} does not require RAG retrieval.")
    return {
        "rag_needed": False,
        "rag_query": None,
        "rag_context": []
    }


async def conditional_rag_retrieval_node(state: LiveMeetingGraphState) -> Dict[str, Any]:
    """
    Node 2: Conditionally retrieves grounding facts/events from RAG database
    (KnowledgeNode / EventNode) using HybridSearchService.
    """
    if not state.get("rag_needed", False):
        return {"rag_context": []}

    query_text = state.get("rag_query") or state.get("current_window_transcript") or ""
    if not query_text.strip():
        return {"rag_context": []}

    retrieved_items = []
    db = None
    try:
        db = SessionLocal()
        search_res = await hybrid_search_service.search(
            db=db,
            query_text=query_text,
            top_k=5
        )
        
        events = search_res.get("events", [])
        for ev in events:
            retrieved_items.append({
                "source": ev.source,
                "title": ev.entity_id or ev.event_type,
                "content": ev.content,
                "timestamp": ev.timestamp.isoformat() if ev.timestamp else None
            })

        logger.info(f"[LIVE GRAPH] Retrieved {len(retrieved_items)} RAG items for live window.")
    except Exception as exc:
        logger.warning(f"[LIVE GRAPH] RAG retrieval encountered error: {exc}. Proceeding without RAG context.")
    finally:
        if db:
            db.close()

    return {"rag_context": retrieved_items}


async def analyze_window_node(state: LiveMeetingGraphState) -> Dict[str, Any]:
    """
    Node 3: Core LLM Window Analyzer.
    Formulates the 4-part Context Window:
      1. Previous Minutes Summary (rolling executive context)
      2. Meeting State (Members, Recent Decisions, Tasks)
      3. Conditional RAG context (if retrieved)
      4. Current 4-Minute Window Transcript
    """
    llm = get_chat_model(temperature=0.1)

    previous_summary = state.get("previous_minutes_summary") or "No previous minutes. Meeting has just started."
    members = state.get("members") or []
    recent_decisions = state.get("recent_decisions") or []
    tasks = state.get("tasks") or []
    rag_context = state.get("rag_context") or []
    current_transcript = state.get("current_window_transcript") or "(Silence or no transcript in this window)"
    window_idx = state.get("window_index", 1)

    # Format RAG block
    rag_block = "None retrieved (not required for this window)."
    if rag_context:
        rag_block = "\n".join([
            f"- [{item.get('source', 'knowledge')}] {item.get('title')}: {item.get('content')}"
            for item in rag_context
        ])

    system_prompt = (
        "You are an expert Real-Time Engineering Meeting Intelligence Assistant.\n"
        "You analyze live meetings in rolling 4-minute windows. Your objective is to extract:\n"
        "1. Executive summary of what was discussed in this specific 4-minute window.\n"
        "2. Any new technical, architectural, or organizational decisions agreed upon in this window.\n"
        "3. Any new or modified actionable tasks / commitments with designated assignees.\n"
        "4. Any speaker or participant names actively talking in this window.\n"
        "5. An updated cumulative summary combining previous minutes with this new window.\n\n"
        "Respond ONLY with a valid JSON object matching this schema:\n"
        "{\n"
        '  "detected_speakers": ["string"],\n'
        '  "window_summary": "concise executive summary of this 4-minute block",\n'
        '  "new_decisions": [\n'
        '    {"decision": "string", "topic": "string", "rationale": "string or null"}\n'
        '  ],\n'
        '  "new_tasks": [\n'
        '    {"task": "string", "assignee": "string", "work_status": "PLANNED", "area": "string"}\n'
        '  ],\n'
        '  "updated_accumulated_summary": "coherent rolling summary from meeting start up to this point"\n'
        "}"
    )

    user_prompt = f"""=== 4-MINUTE LIVE WINDOW #{window_idx} ===

=== 1. PREVIOUS MINUTES SUMMARY ===
{previous_summary}

=== 2. CURRENT MEETING STATE ===
Active Members in Call: {json.dumps(members)}
Decisions Recorded So Far: {json.dumps(recent_decisions)}
Action Items / Tasks Active: {json.dumps(tasks)}

=== 3. RELEVANT EXTERNAL KNOWLEDGE (RAG) ===
{rag_block}

=== 4. CURRENT 4-MINUTE WINDOW TRANSCRIPT ===
{current_transcript}

Analyze the transcript for this 4-minute window in relation to the current meeting state and previous minutes summary. Produce the JSON result:"""

    try:
        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_prompt)
        ]
        
        # Invoke LangChain model with json mode
        response = await llm.ainvoke(
            messages,
            response_format={"type": "json_object"}
        )
        
        content = response.content
        parsed = json.loads(content)
        
        return {
            "extracted_members": parsed.get("detected_speakers") or [],
            "window_summary": parsed.get("window_summary") or f"Summary for window #{window_idx}",
            "new_decisions": parsed.get("new_decisions") or [],
            "new_tasks": parsed.get("new_tasks") or [],
            "accumulated_summary": parsed.get("updated_accumulated_summary") or previous_summary
        }
    except Exception as e:
        logger.error(f"[LIVE GRAPH] Error in analyze_window_node: {e}", exc_info=True)
        # Fallback graceful response
        fallback_summary = f"Discussion occurred in window #{window_idx}."
        return {
            "extracted_members": [],
            "window_summary": fallback_summary,
            "new_decisions": [],
            "new_tasks": [],
            "accumulated_summary": f"{previous_summary}\n- {fallback_summary}" if previous_summary else fallback_summary,
            "error": str(e)
        }


def consolidate_state_node(state: LiveMeetingGraphState) -> Dict[str, Any]:
    """
    Node 4: Merges and reconciles meeting state:
    - Deduplicates and updates members list
    - Appends new decisions avoiding duplicates
    - Appends/updates tasks
    - Prepares state for persistence
    """
    # 1. Consolidate members
    existing_members = set(m.strip() for m in state.get("members", []) if m and m.strip())
    new_members = state.get("extracted_members", []) or []
    for nm in new_members:
        if isinstance(nm, str) and nm.strip() and nm.lower() not in ("unknown", "speaker", "participant"):
            existing_members.add(nm.strip())
    
    updated_members = sorted(list(existing_members))

    # 2. Consolidate decisions (avoid duplicates based on text similarity)
    existing_decisions = list(state.get("recent_decisions", []) or [])
    new_decisions = state.get("new_decisions", []) or []
    
    for nd in new_decisions:
        dec_text = (nd.get("decision") or "").strip().lower()
        if not dec_text:
            continue
        # Check if already in existing
        if not any(dec_text in (ed.get("decision") or "").lower() for ed in existing_decisions):
            existing_decisions.append({
                "decision": nd.get("decision"),
                "topic": nd.get("topic") or "Architecture",
                "rationale": nd.get("rationale"),
                "timestamp": f"Window #{state.get('window_index', 1)}"
            })

    # 3. Consolidate tasks (avoid duplicate tasks)
    existing_tasks = list(state.get("tasks", []) or [])
    new_tasks = state.get("new_tasks", []) or []
    
    for nt in new_tasks:
        task_text = (nt.get("task") or "").strip().lower()
        if not task_text:
            continue
        if not any(task_text in (et.get("task") or "").lower() for et in existing_tasks):
            existing_tasks.append({
                "task": nt.get("task"),
                "assignee": nt.get("assignee") or "Unassigned",
                "work_status": nt.get("work_status") or "PLANNED",
                "area": nt.get("area") or "General"
            })

    # 4. Final accumulated summary
    accumulated_summary = state.get("accumulated_summary") or state.get("window_summary") or ""

    return {
        "members": updated_members,
        "recent_decisions": existing_decisions,
        "tasks": existing_tasks,
        "accumulated_summary": accumulated_summary
    }


# =====================================================================
# 4. LangGraph Graph Assembly & Compilation
# =====================================================================

def should_retrieve_rag(state: LiveMeetingGraphState) -> str:
    """Conditional router function for RAG necessity."""
    if state.get("rag_needed", False):
        return "retrieve_rag_context"
    return "analyze_window"


def build_live_meeting_graph():
    """Builds and compiles the StateGraph for live meeting intelligence."""
    builder = StateGraph(LiveMeetingGraphState)

    builder.add_node("assess_rag_necessity", assess_rag_necessity_node)
    builder.add_node("retrieve_rag_context", conditional_rag_retrieval_node)
    builder.add_node("analyze_window", analyze_window_node)
    builder.add_node("consolidate_state", consolidate_state_node)

    # Flow
    builder.add_edge(START, "assess_rag_necessity")
    
    builder.add_conditional_edges(
        "assess_rag_necessity",
        should_retrieve_rag,
        {
            "retrieve_rag_context": "retrieve_rag_context",
            "analyze_window": "analyze_window"
        }
    )
    
    builder.add_edge("retrieve_rag_context", "analyze_window")
    builder.add_edge("analyze_window", "consolidate_state")
    builder.add_edge("consolidate_state", END)

    return builder.compile()


# Compiled singleton graph
live_meeting_graph = build_live_meeting_graph()
