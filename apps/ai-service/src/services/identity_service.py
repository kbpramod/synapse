import re
from typing import Optional, List, Dict, Any, Tuple
from sqlalchemy.orm import Session

from src.models.person import Person, IdentityLink


class IdentityService:

    def resolve_identity(
        self,
        db: Session,
        raw_identity: str,
        provider: str,  # 'meeting', 'github', 'email', 'jira'
        email: Optional[str] = None,
        organization: Optional[str] = None
    ) -> Tuple[Person, IdentityLink]:
        """
        Resolve a raw identity (speaker name, github handle, email) to a canonical Person entity.
        Returns (Person, IdentityLink).
        """
        raw_identity_clean = raw_identity.strip() if raw_identity else "Unknown"

        # 1. Direct match on existing IdentityLink (exact provider + identity_value)
        link = db.query(IdentityLink).filter(
            IdentityLink.provider == provider,
            IdentityLink.identity_value.ilike(raw_identity_clean)
        ).first()

        if link:
            person = db.query(Person).filter(Person.id == link.person_id).first()
            if person:
                return person, link

        # 2. Match by verified email if provided or if raw_identity looks like email
        target_email = email or (raw_identity_clean if "@" in raw_identity_clean else None)
        if target_email:
            person = db.query(Person).filter(Person.primary_email.ilike(target_email)).first()
            if person:
                new_link = IdentityLink(
                    person_id=person.id,
                    provider=provider,
                    identity_value=raw_identity_clean,
                    state="CONFIRMED" if provider == "email" else "PROBABLE",
                    confidence_score=0.95,
                    evidence_json={
                        "person_id": person.id,
                        "confidence": 0.95,
                        "state": "PROBABLE",
                        "signals": {
                            "email_exact_match": 1.0,
                            "name_similarity": self._name_similarity(raw_identity_clean, person.canonical_name)
                        }
                    }
                )
                db.add(new_link)
                db.commit()
                db.refresh(new_link)
                return person, new_link

        # 3. Fuzzy / Heuristic Name Match across canonical Persons
        all_persons = db.query(Person).all()
        best_person = None
        best_score = 0.0
        best_signals = {}

        for p in all_persons:
            name_sim = self._name_similarity(raw_identity_clean, p.canonical_name)
            org_match = 1.0 if (organization and p.organization == organization) else 0.5
            
            # Combine signals
            combined_score = (name_sim * 0.7) + (org_match * 0.3)
            if combined_score > best_score:
                best_score = combined_score
                best_person = p
                best_signals = {
                    "name_similarity": round(name_sim, 2),
                    "organization_match": round(org_match, 2),
                    "historical_activity": 0.5
                }

        # If fuzzy score is strong enough (> 0.70), link to best person
        if best_person and best_score >= 0.70:
            link_state = "CONFIRMED" if best_score > 0.90 else "PROBABLE"
            new_link = IdentityLink(
                person_id=best_person.id,
                provider=provider,
                identity_value=raw_identity_clean,
                state=link_state,
                confidence_score=round(best_score, 2),
                evidence_json={
                    "person_id": best_person.id,
                    "confidence": round(best_score, 2),
                    "state": link_state,
                    "signals": best_signals
                }
            )
            db.add(new_link)
            db.commit()
            db.refresh(new_link)
            return best_person, new_link

        # 4. Create new Canonical Person if no match
        canonical_name = raw_identity_clean
        if "@" in raw_identity_clean:
            canonical_name = raw_identity_clean.split("@")[0].replace(".", " ").title()

        new_person = Person(
            canonical_name=canonical_name,
            primary_email=target_email,
            organization=organization
        )
        db.add(new_person)
        db.commit()
        db.refresh(new_person)

        new_link = IdentityLink(
            person_id=new_person.id,
            provider=provider,
            identity_value=raw_identity_clean,
            state="CONFIRMED" if provider == "email" else "PROBABLE",
            confidence_score=1.0 if provider == "email" else 0.8,
            evidence_json={
                "person_id": new_person.id,
                "confidence": 1.0 if provider == "email" else 0.8,
                "state": "CONFIRMED" if provider == "email" else "PROBABLE",
                "signals": {
                    "new_canonical_identity": 1.0
                }
            }
        )
        db.add(new_link)
        db.commit()
        db.refresh(new_link)

        return new_person, new_link

    def confirm_identity_link(
        self,
        db: Session,
        link_id: str
    ) -> IdentityLink:
        """Manually confirm an identity link (converting PROBABLE -> CONFIRMED)."""
        link = db.query(IdentityLink).filter(IdentityLink.id == link_id).first()
        if not link:
            raise ValueError(f"Identity link {link_id} not found.")

        link.state = "CONFIRMED"
        link.confidence_score = 1.0
        if link.evidence_json and "signals" in link.evidence_json:
            link.evidence_json["signals"]["manual_confirmation"] = 1.0
            link.evidence_json["state"] = "CONFIRMED"
            link.evidence_json["confidence"] = 1.0

        db.commit()
        db.refresh(link)
        return link

    def link_identities_manually(
        self,
        db: Session,
        person_id: str,
        provider: str,
        identity_value: str
    ) -> IdentityLink:
        """Explicitly link an identity to a person with CONFIRMED state and 1.0 confidence."""
        person = db.query(Person).filter(Person.id == person_id).first()
        if not person:
            raise ValueError(f"Person {person_id} not found.")

        existing_link = db.query(IdentityLink).filter(
            IdentityLink.person_id == person_id,
            IdentityLink.provider == provider,
            IdentityLink.identity_value == identity_value
        ).first()

        if existing_link:
            existing_link.state = "CONFIRMED"
            existing_link.confidence_score = 1.0
            db.commit()
            db.refresh(existing_link)
            return existing_link

        link = IdentityLink(
            person_id=person_id,
            provider=provider,
            identity_value=identity_value,
            state="CONFIRMED",
            confidence_score=1.0,
            evidence_json={
                "person_id": person_id,
                "confidence": 1.0,
                "state": "CONFIRMED",
                "signals": {
                    "explicit_account_link": 1.0
                }
            }
        )
        db.add(link)
        db.commit()
        db.refresh(link)
        return link

    def _name_similarity(self, s1: str, s2: str) -> float:
        """Compute string similarity score between names."""
        n1 = re.sub(r'[^a-zA-Z0-9]', '', s1.lower())
        n2 = re.sub(r'[^a-zA-Z0-9]', '', s2.lower())

        if not n1 or not n2:
            return 0.0
        if n1 == n2:
            return 1.0
        if n1 in n2 or n2 in n1:
            return 0.85

        # Jaccard character n-gram similarity
        set1 = set(n1[i:i+2] for i in range(len(n1)-1)) or set(n1)
        set2 = set(n2[i:i+2] for i in range(len(n2)-1)) or set(n2)

        intersection = len(set1.intersection(set2))
        union = len(set1.union(set2))
        return intersection / union if union > 0 else 0.0


identity_service = IdentityService()
