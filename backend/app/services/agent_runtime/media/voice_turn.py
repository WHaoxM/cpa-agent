"""
Voice Turn lifecycle — browser Web Speech is one STT adapter, not the architecture.

States: capturing → transcribing → ready | failed
Final transcript becomes a voice_transcript content part; optional audio file_id retained.
"""

from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from ....utils.logger import get_logger
from .types import VoiceStatus

logger = get_logger("cpa-agent.agent_runtime.media.voice")

_mem: Dict[str, Dict[str, Any]] = {}
_lock = threading.Lock()


def _voice_root() -> Path:
    try:
        from ....config import Config
        root = Path(Config.UPLOAD_FOLDER) / "voice_turns"
    except Exception:
        root = Path(__file__).resolve().parents[4] / "uploads" / "voice_turns"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _voice_path(turn_id: str) -> Path:
    return _voice_root() / f"{turn_id}.json"


class VoiceTurnService:
    def __init__(self, mysql_client=None):
        self._mysql = mysql_client

    @property
    def mysql(self):
        if self._mysql is None:
            from ...db import get_mysql_client
            self._mysql = get_mysql_client()
        return self._mysql

    def start(
        self,
        *,
        session_id: Optional[str],
        student_id: Optional[str],
        lang: str = "zh-CN",
        stt_provider: str = "browser_web_speech",
    ) -> Dict[str, Any]:
        turn_id = f"vt_{uuid.uuid4().hex[:14]}"
        now = datetime.now().isoformat(sep=" ")
        rec = {
            "turn_id": turn_id,
            "session_id": session_id or "",
            "student_id": student_id,
            "status": VoiceStatus.CAPTURING.value,
            "stt_provider": stt_provider,
            "lang": lang or "zh-CN",
            "transcript": "",
            "interim_transcript": "",
            "confidence": None,
            "audio_file_id": None,
            "error": None,
            "created_at": now,
            "updated_at": now,
        }
        self._save(rec)
        return self.public(rec)

    def update(
        self,
        turn_id: str,
        *,
        interim_transcript: Optional[str] = None,
        transcript: Optional[str] = None,
        is_final: bool = False,
        confidence: Optional[float] = None,
        audio_file_id: Optional[str] = None,
        status: Optional[str] = None,
        error: Optional[str] = None,
        stt_provider: Optional[str] = None,
    ) -> Dict[str, Any]:
        rec = self._load(turn_id)
        if not rec:
            raise KeyError(turn_id)

        if interim_transcript is not None:
            rec["interim_transcript"] = interim_transcript
            if rec["status"] == VoiceStatus.CAPTURING.value:
                rec["status"] = VoiceStatus.TRANSCRIBING.value

        if transcript is not None:
            rec["transcript"] = transcript.strip()

        if confidence is not None:
            rec["confidence"] = confidence
        if audio_file_id:
            rec["audio_file_id"] = audio_file_id
        if stt_provider:
            rec["stt_provider"] = stt_provider
        if error:
            rec["error"] = error[:500]
            rec["status"] = VoiceStatus.FAILED.value
        elif status:
            rec["status"] = status
        elif is_final and rec.get("transcript"):
            rec["status"] = VoiceStatus.READY.value
        elif is_final and not rec.get("transcript"):
            rec["status"] = VoiceStatus.FAILED.value
            rec["error"] = rec.get("error") or "empty transcript"

        rec["updated_at"] = datetime.now().isoformat(sep=" ")
        self._save(rec)
        return self.public(rec)

    def get(self, turn_id: str) -> Optional[Dict[str, Any]]:
        rec = self._load(turn_id)
        return self.public(rec) if rec else None

    def as_content_part(self, turn_id: str) -> Optional[Dict[str, Any]]:
        rec = self._load(turn_id)
        if not rec or rec.get("status") != VoiceStatus.READY.value:
            return None
        return {
            "type": "voice_transcript",
            "text": rec.get("transcript") or "",
            "turn_id": turn_id,
            "file_id": rec.get("audio_file_id") or "",
        }

    def public(self, rec: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "turn_id": rec["turn_id"],
            "session_id": rec.get("session_id"),
            "student_id": rec.get("student_id"),
            "status": rec.get("status"),
            "stt_provider": rec.get("stt_provider"),
            "lang": rec.get("lang"),
            "transcript": rec.get("transcript") or "",
            "interim_transcript": rec.get("interim_transcript") or "",
            "confidence": rec.get("confidence"),
            "audio_file_id": rec.get("audio_file_id"),
            "error": rec.get("error"),
            "created_at": rec.get("created_at"),
            "updated_at": rec.get("updated_at"),
        }

    def _save(self, rec: Dict[str, Any]) -> None:
        with _lock:
            _mem[rec["turn_id"]] = dict(rec)
        try:
            _voice_path(rec["turn_id"]).write_text(
                json.dumps(rec, ensure_ascii=False), encoding="utf-8",
            )
        except Exception as e:
            logger.info("voice_turn disk skip: %s", e)
        try:
            self.mysql.execute(
                """
                INSERT INTO voice_turn
                (turn_id, session_id, student_id, status, stt_provider, lang,
                 transcript, interim_transcript, confidence, audio_file_id, error,
                 created_at, updated_at)
                VALUES
                (:turn_id, :session_id, :student_id, :status, :stt_provider, :lang,
                 :transcript, :interim_transcript, :confidence, :audio_file_id, :error,
                 :created_at, :updated_at)
                ON DUPLICATE KEY UPDATE
                  status=VALUES(status), transcript=VALUES(transcript),
                  interim_transcript=VALUES(interim_transcript),
                  confidence=VALUES(confidence), audio_file_id=VALUES(audio_file_id),
                  error=VALUES(error), updated_at=VALUES(updated_at),
                  stt_provider=VALUES(stt_provider)
                """,
                {
                    "turn_id": rec["turn_id"],
                    "session_id": rec.get("session_id") or "",
                    "student_id": rec.get("student_id"),
                    "status": rec.get("status"),
                    "stt_provider": rec.get("stt_provider"),
                    "lang": rec.get("lang"),
                    "transcript": rec.get("transcript") or "",
                    "interim_transcript": rec.get("interim_transcript") or "",
                    "confidence": rec.get("confidence"),
                    "audio_file_id": rec.get("audio_file_id"),
                    "error": rec.get("error"),
                    "created_at": rec.get("created_at"),
                    "updated_at": rec.get("updated_at"),
                },
                commit=True,
            )
        except Exception as e:
            logger.info("voice_turn mysql skip: %s", e)

    def _load(self, turn_id: str) -> Optional[Dict[str, Any]]:
        with _lock:
            if turn_id in _mem:
                return dict(_mem[turn_id])
        vp = _voice_path(turn_id)
        if vp.is_file():
            try:
                d = json.loads(vp.read_text(encoding="utf-8"))
                with _lock:
                    _mem[turn_id] = d
                return dict(d)
            except Exception:
                pass
        try:
            row = self.mysql.fetch_one(
                "SELECT * FROM voice_turn WHERE turn_id = :tid LIMIT 1",
                {"tid": turn_id},
            )
            if not row:
                return None
            d = dict(row)
            with _lock:
                _mem[turn_id] = d
            return d
        except Exception:
            return None


_svc: Optional[VoiceTurnService] = None
_slock = threading.Lock()


def get_voice_turn_service() -> VoiceTurnService:
    global _svc
    if _svc is None:
        with _slock:
            if _svc is None:
                _svc = VoiceTurnService()
    return _svc
