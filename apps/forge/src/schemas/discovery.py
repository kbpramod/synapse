from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class BoundingBox(BaseModel):
    x: int
    y: int
    width: int
    height: int


# Three Canonical Fixed Viewports for Responsive Testing
FIXED_VIEWPORTS: Dict[str, Dict[str, int]] = {
    "desktop": {"width": 1280, "height": 800},
    "tablet": {"width": 768, "height": 1024},
    "mobile": {"width": 375, "height": 667},
}


class ButtonElement(BaseModel):
    forge_id: Optional[str] = None
    text: str
    role: str = "button"
    type: str = "button"
    id: Optional[str] = None
    name: Optional[str] = None
    visible: bool = True
    enabled: bool = True
    in_viewport: bool = False
    visible_viewports: List[str] = Field(default_factory=lambda: ["desktop"])
    viewport_visibility: Dict[str, bool] = Field(default_factory=dict)
    selector: str
    bounding_box: Optional[BoundingBox] = None


class InputElement(BaseModel):
    forge_id: Optional[str] = None
    type: str = "text"
    name: Optional[str] = None
    id: Optional[str] = None
    placeholder: Optional[str] = None
    label: Optional[str] = None
    value: Optional[str] = None
    required: bool = False
    disabled: bool = False
    checked: Optional[bool] = None
    visible: bool = True
    in_viewport: bool = False
    visible_viewports: List[str] = Field(default_factory=lambda: ["desktop"])
    viewport_visibility: Dict[str, bool] = Field(default_factory=dict)
    selector: str
    bounding_box: Optional[BoundingBox] = None


class TextareaElement(BaseModel):
    forge_id: Optional[str] = None
    name: Optional[str] = None
    id: Optional[str] = None
    placeholder: Optional[str] = None
    label: Optional[str] = None
    value: Optional[str] = None
    required: bool = False
    disabled: bool = False
    visible: bool = True
    in_viewport: bool = False
    visible_viewports: List[str] = Field(default_factory=lambda: ["desktop"])
    viewport_visibility: Dict[str, bool] = Field(default_factory=dict)
    selector: str
    bounding_box: Optional[BoundingBox] = None


class SelectOption(BaseModel):
    text: str
    value: str
    selected: bool = False


class SelectElement(BaseModel):
    forge_id: Optional[str] = None
    name: Optional[str] = None
    id: Optional[str] = None
    label: Optional[str] = None
    options: List[SelectOption] = Field(default_factory=list)
    disabled: bool = False
    required: bool = False
    visible: bool = True
    in_viewport: bool = False
    visible_viewports: List[str] = Field(default_factory=lambda: ["desktop"])
    viewport_visibility: Dict[str, bool] = Field(default_factory=dict)
    selector: str
    bounding_box: Optional[BoundingBox] = None


class LinkElement(BaseModel):
    forge_id: Optional[str] = None
    text: str
    href: str
    raw_href: Optional[str] = None
    id: Optional[str] = None
    target: Optional[str] = None
    visible: bool = True
    in_viewport: bool = False
    visible_viewports: List[str] = Field(default_factory=lambda: ["desktop"])
    viewport_visibility: Dict[str, bool] = Field(default_factory=dict)
    selector: str
    bounding_box: Optional[BoundingBox] = None


class FormElement(BaseModel):
    id: Optional[str] = None
    name: Optional[str] = None
    action: Optional[str] = None
    method: str = "GET"
    input_count: int = 0
    button_count: int = 0
    visible: bool = True
    in_viewport: bool = False
    visible_viewports: List[str] = Field(default_factory=lambda: ["desktop"])
    viewport_visibility: Dict[str, bool] = Field(default_factory=dict)
    selector: str
    bounding_box: Optional[BoundingBox] = None


class DialogElement(BaseModel):
    id: Optional[str] = None
    role: str = "dialog"
    title: Optional[str] = None
    visible: bool = False
    in_viewport: bool = False
    visible_viewports: List[str] = Field(default_factory=lambda: ["desktop"])
    viewport_visibility: Dict[str, bool] = Field(default_factory=dict)
    selector: str
    bounding_box: Optional[BoundingBox] = None


class HeadingElement(BaseModel):
    level: str  # h1, h2, h3, etc.
    text: str


class ImageElement(BaseModel):
    alt: Optional[str] = None
    src: Optional[str] = None
    visible: bool = True
    in_viewport: bool = False
    visible_viewports: List[str] = Field(default_factory=lambda: ["desktop"])
    viewport_visibility: Dict[str, bool] = Field(default_factory=dict)
    selector: str
    bounding_box: Optional[BoundingBox] = None


class ViewportSummary(BaseModel):
    desktop_only_count: int = 0
    tablet_only_count: int = 0
    mobile_only_count: int = 0
    all_viewports_count: int = 0
    desktop_only_selectors: List[str] = Field(default_factory=list)
    mobile_only_selectors: List[str] = Field(default_factory=list)
    tablet_only_selectors: List[str] = Field(default_factory=list)


class DiscoveredElements(BaseModel):
    buttons: List[ButtonElement] = Field(default_factory=list)
    inputs: List[InputElement] = Field(default_factory=list)
    links: List[LinkElement] = Field(default_factory=list)
    textareas: List[TextareaElement] = Field(default_factory=list)
    selects: List[SelectElement] = Field(default_factory=list)
    forms: List[FormElement] = Field(default_factory=list)
    dialogs: List[DialogElement] = Field(default_factory=list)
    headings: List[HeadingElement] = Field(default_factory=list)
    images: List[ImageElement] = Field(default_factory=list)
    viewports_summary: Optional[ViewportSummary] = None


class Viewport(BaseModel):
    width: int
    height: int


class PageInfo(BaseModel):
    url: str
    title: str
    slug: Optional[str] = None
    path: Optional[str] = None
    description: Optional[str] = None
    viewport: Optional[Viewport] = None


class ConsoleMessage(BaseModel):
    type: str
    text: str
    location: Optional[Dict[str, Any]] = None


class FailedRequest(BaseModel):
    url: str
    method: Optional[str] = None
    status: Optional[int] = None
    status_text: Optional[str] = None
    failure: Optional[str] = None


class RuntimeInfo(BaseModel):
    console_errors: List[ConsoleMessage] = Field(default_factory=list)
    failed_requests: List[FailedRequest] = Field(default_factory=list)


class TextSummary(BaseModel):
    headings: List[HeadingElement] = Field(default_factory=list)
    body_text_preview: str = ""


class StateInfo(BaseModel):
    """
    Represents the page's current authentication, user role, and UI state.
    Designed to be editable and expandable as the agent observes new state transitions.
    """
    name: str = "default"  # e.g., 'default', 'guest', 'logged_in', 'admin_view', 'modal_open'
    role: str = "guest"  # e.g., 'guest', 'user', 'admin'
    is_authenticated: bool = False
    requires_auth: bool = False
    description: Optional[str] = None
    custom_state_data: Dict[str, Any] = Field(default_factory=dict)


class PageViewState(BaseModel):
    """Snapshot of a page's elements and text under a specific state/role."""
    state: StateInfo
    elements: DiscoveredElements
    text: Optional[TextSummary] = None
    timestamp: str = Field(default_factory=lambda: datetime.utcnow().isoformat())


class DiscoveryResult(BaseModel):
    """
    Single-page discovery result.
    Includes active state & elements, plus an expandable dictionary of known states.
    """
    page: PageInfo
    state: StateInfo = Field(default_factory=StateInfo)
    elements: DiscoveredElements
    text: TextSummary
    runtime: RuntimeInfo
    # Expandable multi-state registry for this page (e.g. "guest", "authenticated", "admin")
    states: Dict[str, PageViewState] = Field(default_factory=dict)
    timestamp: str = Field(default_factory=lambda: datetime.utcnow().isoformat())


# --- Multi-Page Discovery Schemas ---

class PageNode(BaseModel):
    url: str
    title: str
    slug: str
    depth: int
    discovered_from: Optional[str] = None
    page_folder: str  # e.g. "pages/about/"
    page_file: str  # e.g. "pages/about/index.json"
    element_counts: Dict[str, int]
    runtime_issue_count: int
    outbound_internal_links: List[str] = Field(default_factory=list)


class SiteDiscoveryConfig(BaseModel):
    max_pages: int = 15
    max_depth: int = 3
    same_domain_only: bool = True
    excluded_patterns: List[str] = Field(default_factory=lambda: [
        "logout", "signout", "delete", "destroy", "*.pdf", "*.zip", "*.png", "*.jpg", "*.mp4"
    ])
    settle_ms: int = 1500
    timeout_ms: int = 30000
    save_to_storage: bool = True
    default_role: str = "guest"
    is_authenticated: bool = False


class SiteDiscoveryResult(BaseModel):
    domain: str
    start_url: str
    total_pages_discovered: int
    max_depth_reached: int
    pages: List[PageNode] = Field(default_factory=list)
    graph: Dict[str, List[str]] = Field(default_factory=dict)  # url -> list of child urls
    timestamp: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
