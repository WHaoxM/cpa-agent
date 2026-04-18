#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
import torch
from transformers import AutoModelForCausalLM, AutoTokenizer

work_dir = Path(__file__).resolve().parent
if str(work_dir) not in sys.path:
    sys.path.insert(0, str(work_dir))

ROOT = work_dir.parents[1]
DATA_DIR = ROOT / "data"
OUTPUT_FILE = DATA_DIR / "pure_llm_benchmark_report.json"

try:
    from .phase1_physical_layer import flatten_mineru_v2, parse_mineru_v2_item_text
except ImportError:
    from phase1_physical_layer import flatten_mineru_v2, parse_mineru_v2_item_text

ENTITY_TYPES = [
    "PERSON_NAME",
    "EMAIL",
    "POSITION",
    "SCHOOL",
    "MAJOR",
    "DEGREE",
    "COMPANY",
    "PROJECT_NAME",
    "PROJECT_ROLE",
    "SKILL",
    "DATE_RANGE",
]

RELATION_TYPES = [
    "HAS_TITLE",
    "HAS_ROLE",
    "HAS_PERIOD",
    "HAS_MAJOR",
    "HAS_DEGREE",
    "HAS_SKILL",
]

SAMPLE_REGISTRY = {
    "zhong_zimin": {
        "mineru_v2": DATA_DIR / "mineru_output" / "简历" / "简历" / "auto" / "简历_content_list_v2.json",
        "train_json": DATA_DIR / "resume" / "train_data" / "1.json",
    }
}


def normalize_span(text: str) -> str:
    value = (text or "").strip().lower()
    value = value.replace("（", "(").replace("）", ")")
    value = value.replace("—", "-").replace("–", "-").replace("~", "-")
    value = value.replace("/", "-")
    value = re.sub(r"\s+", "", value)
    return value


def extract_first_json_block(text: str) -> str:
    if not text:
        return ""
    content = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    fenced = re.findall(r"```(?:json)?\s*(.*?)```", content, flags=re.S)
    for block in fenced:
        candidate = block.strip()
        if candidate.startswith("{") and candidate.endswith("}"):
            return candidate
    start = content.find("{")
    if start < 0:
        return ""
    depth = 0
    in_string = False
    escape = False
    for index in range(start, len(content)):
        ch = content[index]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return content[start:index + 1]
    return content[start:].strip()


def parse_json_output(raw_text: str) -> Tuple[Dict[str, Any], bool, Optional[str]]:
    try:
        return json.loads(raw_text), True, None
    except Exception:
        pass
    json_block = extract_first_json_block(raw_text)
    if not json_block:
        return {}, False, "no_json_object_found"
    try:
        return json.loads(json_block), True, None
    except Exception as exc:
        return {}, False, str(exc)


def dedupe_entities(entities: List[Dict[str, str]]) -> List[Dict[str, str]]:
    seen = set()
    result = []
    for entity in entities:
        entity_type = (entity.get("type") or "").strip().upper()
        span = (entity.get("span") or "").strip()
        key = (entity_type, normalize_span(span))
        if not entity_type or not span or key in seen:
            continue
        seen.add(key)
        result.append({"type": entity_type, "span": span})
    return result


def dedupe_relations(relations: List[Dict[str, str]]) -> List[Dict[str, str]]:
    seen = set()
    result = []
    for relation in relations:
        relation_type = (relation.get("relation_type") or "").strip().upper()
        head_type = (relation.get("head_type") or "").strip().upper()
        tail_type = (relation.get("tail_type") or "").strip().upper()
        head_span = (relation.get("head_span") or "").strip()
        tail_span = (relation.get("tail_span") or "").strip()
        key = (
            relation_type,
            head_type,
            normalize_span(head_span),
            tail_type,
            normalize_span(tail_span),
        )
        if not relation_type or not head_span or not tail_span or key in seen:
            continue
        seen.add(key)
        result.append(
            {
                "relation_type": relation_type,
                "head_type": head_type,
                "head_span": head_span,
                "tail_type": tail_type,
                "tail_span": tail_span,
            }
        )
    return result


def coerce_entities(parsed: Dict[str, Any]) -> List[Dict[str, str]]:
    raw_entities = parsed.get("entities") or parsed.get("items") or []
    result = []
    for item in raw_entities:
        if not isinstance(item, dict):
            continue
        entity_type = item.get("type") or item.get("entity_type") or item.get("label") or ""
        span = item.get("span") or item.get("span_text") or item.get("text") or item.get("value") or ""
        result.append({"type": str(entity_type), "span": str(span)})
    return dedupe_entities(result)


def coerce_relations(parsed: Dict[str, Any]) -> List[Dict[str, str]]:
    raw_relations = parsed.get("relations") or parsed.get("items") or []
    result = []
    for item in raw_relations:
        if not isinstance(item, dict):
            continue
        relation_type = item.get("relation_type") or item.get("type") or item.get("label") or ""
        head = item.get("head")
        tail = item.get("tail")
        if isinstance(head, dict):
            head_type = head.get("type") or head.get("entity_type") or ""
            head_span = head.get("span") or head.get("span_text") or head.get("text") or ""
        else:
            head_type = item.get("head_type") or ""
            head_span = item.get("head_span") or item.get("head_text") or item.get("head_entity") or head or ""
        if isinstance(tail, dict):
            tail_type = tail.get("type") or tail.get("entity_type") or ""
            tail_span = tail.get("span") or tail.get("span_text") or tail.get("text") or ""
        else:
            tail_type = item.get("tail_type") or ""
            tail_span = item.get("tail_span") or item.get("tail_text") or item.get("tail_entity") or tail or ""
        result.append(
            {
                "relation_type": str(relation_type),
                "head_type": str(head_type),
                "head_span": str(head_span),
                "tail_type": str(tail_type),
                "tail_span": str(tail_span),
            }
        )
    return dedupe_relations(result)


def load_raw_resume_text(mineru_v2_path: Path) -> str:
    payload = json.loads(mineru_v2_path.read_text(encoding="utf-8"))
    items = flatten_mineru_v2(payload)
    lines = []
    for item in items:
        text = parse_mineru_v2_item_text(item)
        if text:
            lines.append(text)
    return "\n".join(lines)


def build_period(duration: Dict[str, Any]) -> Optional[str]:
    start = duration.get("start")
    end = duration.get("end")
    if not start:
        return None
    start_text = str(start).replace("-", "/")
    if end in (None, ""):
        return start_text
    if str(end) == "至今":
        return f"{start_text}-至今"
    return f"{start_text}-{str(end).replace('-', '/')}"


def build_ground_truth(train_json_path: Path) -> Dict[str, Any]:
    data = json.loads(train_json_path.read_text(encoding="utf-8"))
    entities: List[Dict[str, str]] = []
    relations: List[Dict[str, str]] = []

    def add_entity(entity_type: str, span: Optional[str]) -> None:
        if span:
            entities.append({"type": entity_type, "span": str(span)})

    def add_relation(head_type: str, head_span: Optional[str], relation_type: str, tail_type: str, tail_span: Optional[str]) -> None:
        if head_span and tail_span:
            relations.append(
                {
                    "relation_type": relation_type,
                    "head_type": head_type,
                    "head_span": str(head_span),
                    "tail_type": tail_type,
                    "tail_span": str(tail_span),
                }
            )

    personal_info = data.get("personal_info", {})
    add_entity("PERSON_NAME", personal_info.get("name"))
    add_entity("EMAIL", personal_info.get("contact", {}).get("email"))
    add_entity("POSITION", personal_info.get("position"))

    for education in data.get("education", []):
        school = education.get("school")
        major = education.get("major")
        degree = education.get("degree")
        period = build_period(education.get("duration", {}))
        add_entity("SCHOOL", school)
        add_entity("MAJOR", major)
        add_entity("DEGREE", degree)
        add_entity("DATE_RANGE", period)
        add_relation("SCHOOL", school, "HAS_MAJOR", "MAJOR", major)
        add_relation("SCHOOL", school, "HAS_DEGREE", "DEGREE", degree)
        add_relation("SCHOOL", school, "HAS_PERIOD", "DATE_RANGE", period)

    for work in data.get("work_experience", []):
        company = work.get("company")
        position = work.get("position")
        period = build_period(work.get("duration", {}))
        add_entity("COMPANY", company)
        add_entity("POSITION", position)
        add_entity("DATE_RANGE", period)
        add_relation("COMPANY", company, "HAS_ROLE", "POSITION", position)
        add_relation("COMPANY", company, "HAS_PERIOD", "DATE_RANGE", period)
        for skill in work.get("technologies_used", []):
            add_entity("SKILL", skill)
            add_relation("COMPANY", company, "HAS_SKILL", "SKILL", skill)

    for project in data.get("project_experience", []):
        project_name = project.get("name")
        project_role = project.get("role")
        period = build_period(project.get("duration", {}))
        add_entity("PROJECT_NAME", project_name)
        add_entity("PROJECT_ROLE", project_role)
        add_entity("DATE_RANGE", period)
        add_relation("PROJECT_NAME", project_name, "HAS_ROLE", "PROJECT_ROLE", project_role)
        add_relation("PROJECT_NAME", project_name, "HAS_PERIOD", "DATE_RANGE", period)
        for skill in project.get("technologies", []):
            add_entity("SKILL", skill)
            add_relation("PROJECT_NAME", project_name, "HAS_SKILL", "SKILL", skill)

    skills = data.get("skills", {})
    for key in ["programming_languages", "frameworks", "databases", "tools", "ai_ml"]:
        for skill in skills.get(key, []):
            add_entity("SKILL", skill)

    return {
        "sample_id": data.get("resume_id", train_json_path.stem),
        "entities": dedupe_entities(entities),
        "relations": dedupe_relations(relations),
    }


def compute_entity_f1(predicted: List[Dict[str, str]], ground_truth: List[Dict[str, str]]) -> Tuple[float, float, float, Dict[str, float]]:
    if not predicted and not ground_truth:
        return 1.0, 1.0, 1.0, {}
    matched_gt = set()
    tp = 0
    fp = 0
    by_type_pairs: Dict[str, Dict[str, int]] = {}
    for pred in predicted:
        pred_type = pred["type"].upper()
        pred_span = normalize_span(pred["span"])
        matched = False
        for index, gt in enumerate(ground_truth):
            if index in matched_gt:
                continue
            if pred_type == gt["type"].upper() and pred_span == normalize_span(gt["span"]):
                matched_gt.add(index)
                tp += 1
                matched = True
                stats = by_type_pairs.setdefault(pred_type, {"tp": 0, "fp": 0, "fn": 0})
                stats["tp"] += 1
                break
        if not matched:
            fp += 1
            stats = by_type_pairs.setdefault(pred_type, {"tp": 0, "fp": 0, "fn": 0})
            stats["fp"] += 1
    for index, gt in enumerate(ground_truth):
        if index not in matched_gt:
            stats = by_type_pairs.setdefault(gt["type"].upper(), {"tp": 0, "fp": 0, "fn": 0})
            stats["fn"] += 1
    fn = len(ground_truth) - tp
    precision = tp / (tp + fp) if tp + fp > 0 else 0.0
    recall = tp / (tp + fn) if tp + fn > 0 else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall > 0 else 0.0
    f1_by_type = {}
    for entity_type, stats in by_type_pairs.items():
        type_precision = stats["tp"] / (stats["tp"] + stats["fp"]) if stats["tp"] + stats["fp"] > 0 else 0.0
        type_recall = stats["tp"] / (stats["tp"] + stats["fn"]) if stats["tp"] + stats["fn"] > 0 else 0.0
        type_f1 = 2 * type_precision * type_recall / (type_precision + type_recall) if type_precision + type_recall > 0 else 0.0
        f1_by_type[entity_type] = round(type_f1, 4)
    return round(precision, 4), round(recall, 4), round(f1, 4), f1_by_type


def compute_relation_f1(predicted: List[Dict[str, str]], ground_truth: List[Dict[str, str]]) -> Tuple[float, float, float]:
    if not predicted and not ground_truth:
        return 1.0, 1.0, 1.0
    matched_gt = set()
    tp = 0
    fp = 0
    for pred in predicted:
        matched = False
        pred_key = (
            pred["relation_type"].upper(),
            pred["head_type"].upper(),
            normalize_span(pred["head_span"]),
            pred["tail_type"].upper(),
            normalize_span(pred["tail_span"]),
        )
        for index, gt in enumerate(ground_truth):
            if index in matched_gt:
                continue
            gt_key = (
                gt["relation_type"].upper(),
                gt["head_type"].upper(),
                normalize_span(gt["head_span"]),
                gt["tail_type"].upper(),
                normalize_span(gt["tail_span"]),
            )
            if pred_key == gt_key:
                matched_gt.add(index)
                tp += 1
                matched = True
                break
        if not matched:
            fp += 1
    fn = len(ground_truth) - tp
    precision = tp / (tp + fp) if tp + fp > 0 else 0.0
    recall = tp / (tp + fn) if tp + fn > 0 else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall > 0 else 0.0
    return round(precision, 4), round(recall, 4), round(f1, 4)


def build_entity_messages(raw_text: str) -> List[Dict[str, str]]:
    system_prompt = (
        "You are an information extraction model. Extract entities only from the provided resume text. "
        "Do not infer missing facts. Copy spans exactly from the input text. Return JSON only."
    )
    user_prompt = (
        "Task: extract entities from the resume text.\n"
        f"Allowed entity types: {', '.join(ENTITY_TYPES)}\n"
        "Output schema:\n"
        "{\n"
        '  "entities": [\n'
        '    {"type": "ENTITY_TYPE", "span": "EXACT_TEXT_SPAN"}\n'
        "  ]\n"
        "}\n"
        "Rules:\n"
        "1. span must be copied exactly from the input text.\n"
        "2. if an entity is not explicitly present, omit it.\n"
        "3. do not output relations.\n"
        "4. do not normalize or rewrite dates, names, or skills.\n\n"
        "Resume text:\n"
        f"{raw_text}"
    )
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def build_relation_messages(raw_text: str, entities: List[Dict[str, str]]) -> List[Dict[str, str]]:
    system_prompt = (
        "You are an information extraction model. Extract relations only from the provided resume text and the provided entity list. "
        "Do not invent missing entities. Return JSON only."
    )
    entity_json = json.dumps(entities, ensure_ascii=False, indent=2)
    user_prompt = (
        "Task: extract relations between the provided entities.\n"
        f"Allowed relation types: {', '.join(RELATION_TYPES)}\n"
        "Output schema:\n"
        "{\n"
        '  "relations": [\n'
        '    {"relation_type": "RELATION_TYPE", "head_type": "ENTITY_TYPE", "head_span": "EXACT_TEXT_SPAN", "tail_type": "ENTITY_TYPE", "tail_span": "EXACT_TEXT_SPAN"}\n'
        "  ]\n"
        "}\n"
        "Rules:\n"
        "1. use only the entity spans listed below.\n"
        "2. if the relation is not explicitly supported by the text, omit it.\n"
        "3. do not output entities.\n\n"
        "Entities:\n"
        f"{entity_json}\n\n"
        "Resume text:\n"
        f"{raw_text}"
    )
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


class LocalQwenClient:
    def __init__(self, model_path: Path):
        self.model_path = model_path
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.tokenizer = AutoTokenizer.from_pretrained(str(model_path), trust_remote_code=True, padding_side="left")
        load_kwargs: Dict[str, Any] = {"trust_remote_code": True}
        if self.device == "cuda":
            load_kwargs["torch_dtype"] = torch.float16
            load_kwargs["device_map"] = "auto"
            load_kwargs["low_cpu_mem_usage"] = True
        else:
            load_kwargs["torch_dtype"] = torch.float32
        self.model = AutoModelForCausalLM.from_pretrained(str(model_path), **load_kwargs)
        if self.device != "cuda":
            self.model.to(self.device)
        self.model.eval()

    def generate(self, messages: List[Dict[str, str]], thinking_enabled: bool, max_new_tokens: int) -> str:
        try:
            prompt = self.tokenizer.apply_chat_template(
                messages,
                tokenize=False,
                add_generation_prompt=True,
                enable_thinking=thinking_enabled,
            )
        except TypeError:
            prompt = self.tokenizer.apply_chat_template(
                messages,
                tokenize=False,
                add_generation_prompt=True,
            )
        inputs = self.tokenizer(prompt, return_tensors="pt")
        input_device = next(self.model.parameters()).device
        inputs = {key: value.to(input_device) for key, value in inputs.items()}
        pad_token_id = self.tokenizer.pad_token_id or self.tokenizer.eos_token_id
        with torch.inference_mode():
            outputs = self.model.generate(
                **inputs,
                do_sample=False,
                max_new_tokens=max_new_tokens,
                pad_token_id=pad_token_id,
                eos_token_id=self.tokenizer.eos_token_id,
            )
        generated = outputs[0][inputs["input_ids"].shape[1]:]
        return self.tokenizer.decode(generated, skip_special_tokens=True)


class DeepSeekClient:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.endpoint = "https://api.deepseek.com/v1/chat/completions"

    def generate(self, messages: List[Dict[str, str]], thinking_enabled: bool, max_new_tokens: int) -> str:
        model_name = "deepseek-reasoner" if thinking_enabled else "deepseek-chat"
        response = requests.post(
            self.endpoint,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model_name,
                "messages": messages,
                "temperature": 0.1,
                "max_tokens": max_new_tokens,
                "top_p": 0.95,
            },
            timeout=120,
        )
        response.raise_for_status()
        data = response.json()
        message = data["choices"][0]["message"]
        reasoning = message.get("reasoning_content")
        content = message.get("content", "")
        if reasoning:
            return f"<think>\n{reasoning}\n</think>\n\n{content}"
        return content


class OpenAICompatibleClient:
    def __init__(self, base_url: str, model_name: str, api_key: str = "EMPTY"):
        endpoint = base_url.rstrip("/")
        if not endpoint.endswith("/chat/completions"):
            endpoint = f"{endpoint}/chat/completions"
        self.endpoint = endpoint
        self.model_name = model_name
        self.api_key = api_key or "EMPTY"

    def generate(self, messages: List[Dict[str, str]], thinking_enabled: bool, max_new_tokens: int) -> str:
        payload: Dict[str, Any] = {
            "model": self.model_name,
            "messages": messages,
            "temperature": 0.1,
            "max_tokens": max_new_tokens,
            "top_p": 0.95,
            "top_k": 20,
        }
        if not thinking_enabled:
            payload["chat_template_kwargs"] = {"enable_thinking": False}

        response = requests.post(
            self.endpoint,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=120,
        )
        response.raise_for_status()
        data = response.json()
        message = data["choices"][0]["message"]
        reasoning = message.get("reasoning_content")
        content = message.get("content", "")
        if reasoning:
            return f"<think>\n{reasoning}\n</think>\n\n{content}"
        return content


def build_failed_result(
    sample_id: str,
    provider: str,
    model_name: str,
    task: str,
    thinking_enabled: bool,
    error: str,
) -> Dict[str, Any]:
    return {
        "sample_id": sample_id,
        "provider": provider,
        "model_name": model_name,
        "task": task,
        "thinking_enabled": thinking_enabled,
        "status": "FAIL",
        "latency_ms": 0.0,
        "parse_success": False,
        "item_count": 0,
        "precision": 0.0,
        "recall": 0.0,
        "f1": 0.0,
        "f1_by_type": {},
        "raw_output": "",
        "parsed_output": {},
        "error": error,
    }


def build_provider_client(provider: str, provider_arg: Any, args: argparse.Namespace) -> Any:
    if provider == "deepseek":
        return DeepSeekClient(provider_arg)

    try:
        return LocalQwenClient(provider_arg)
    except Exception as exc:
        if provider == "qwen35" and args.qwen35_base_url:
            print(f"[fallback] qwen35 本地直载失败，改用 OpenAI-compatible 服务: {args.qwen35_base_url}")
            return OpenAICompatibleClient(
                base_url=args.qwen35_base_url,
                model_name=args.qwen35_model_name,
                api_key=args.qwen35_api_key,
            )
        if provider == "qwen35":
            raise RuntimeError(
                "Qwen3.5-4B 本地直载失败：当前环境的 Transformers 不支持 `qwen3_5` 架构。"
                "请升级到支持 Qwen3.5 的 Transformers 版本，或提供 --qwen35-base-url 指向本地 OpenAI-compatible 服务。"
                f" 原始错误: {exc}"
            ) from exc
        raise


def run_single_task(
    sample_id: str,
    raw_text: str,
    ground_truth: Dict[str, Any],
    client: Any,
    provider: str,
    model_name: str,
    task: str,
    thinking_enabled: bool,
    max_new_tokens: int,
) -> Dict[str, Any]:
    if task == "entity":
        messages = build_entity_messages(raw_text)
    else:
        messages = build_relation_messages(raw_text, ground_truth["entities"])
    start_time = time.time()
    try:
        raw_output = client.generate(messages, thinking_enabled=thinking_enabled, max_new_tokens=max_new_tokens)
        latency_ms = (time.time() - start_time) * 1000
        parsed, parse_success, parse_error = parse_json_output(raw_output)
        if task == "entity":
            predicted_items = coerce_entities(parsed)
            precision, recall, f1, f1_by_type = compute_entity_f1(predicted_items, ground_truth["entities"])
        else:
            predicted_items = coerce_relations(parsed)
            precision, recall, f1 = compute_relation_f1(predicted_items, ground_truth["relations"])
            f1_by_type = {}
        return {
            "sample_id": sample_id,
            "provider": provider,
            "model_name": model_name,
            "task": task,
            "thinking_enabled": thinking_enabled,
            "status": "SUCCESS",
            "latency_ms": round(latency_ms, 2),
            "parse_success": parse_success,
            "item_count": len(predicted_items),
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "f1_by_type": f1_by_type,
            "raw_output": raw_output,
            "parsed_output": parsed,
            "error": parse_error,
        }
    except Exception as exc:
        latency_ms = (time.time() - start_time) * 1000
        return {
            "sample_id": sample_id,
            "provider": provider,
            "model_name": model_name,
            "task": task,
            "thinking_enabled": thinking_enabled,
            "status": "FAIL",
            "latency_ms": round(latency_ms, 2),
            "parse_success": False,
            "item_count": 0,
            "precision": 0.0,
            "recall": 0.0,
            "f1": 0.0,
            "f1_by_type": {},
            "raw_output": "",
            "parsed_output": {},
            "error": str(exc),
        }


def summarize_results(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    summary_rows = []
    for result in results:
        summary_rows.append(
            {
                "provider": result["provider"],
                "model_name": result["model_name"],
                "task": result["task"],
                "thinking_enabled": result["thinking_enabled"],
                "status": result["status"],
                "parse_success": result["parse_success"],
                "f1": result["f1"],
                "latency_ms": result["latency_ms"],
                "item_count": result["item_count"],
            }
        )
    return {
        "total_runs": len(results),
        "success_runs": sum(1 for item in results if item["status"] == "SUCCESS"),
        "failed_runs": sum(1 for item in results if item["status"] == "FAIL"),
        "parse_success_runs": sum(1 for item in results if item["parse_success"]),
        "rows": summary_rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sample-id", default="zhong_zimin")
    parser.add_argument("--providers", nargs="+", default=["qwen35", "qwen06", "deepseek"])
    parser.add_argument("--tasks", nargs="+", default=["entity", "relation"])
    parser.add_argument("--max-new-tokens", type=int, default=2048)
    parser.add_argument("--output", type=Path, default=OUTPUT_FILE)
    parser.add_argument("--qwen35-path", type=Path, default=ROOT / "model" / "Qwen3.5-4B")
    parser.add_argument("--qwen35-base-url", default=os.environ.get("QWEN35_BASE_URL") or os.environ.get("OPENAI_BASE_URL"))
    parser.add_argument("--qwen35-api-key", default=os.environ.get("QWEN35_API_KEY") or os.environ.get("OPENAI_API_KEY") or "EMPTY")
    parser.add_argument("--qwen35-model-name", default=os.environ.get("QWEN35_MODEL_NAME") or "Qwen/Qwen3.5-4B")
    parser.add_argument("--qwen06-path", type=Path, default=ROOT / "model" / "SmartResume" / "Qwen3-0.6B")
    args = parser.parse_args()

    sample_cfg = SAMPLE_REGISTRY.get(args.sample_id)
    if sample_cfg is None:
        raise ValueError(f"未知 sample_id: {args.sample_id}")

    raw_text = load_raw_resume_text(sample_cfg["mineru_v2"])
    ground_truth = build_ground_truth(sample_cfg["train_json"])
    results: List[Dict[str, Any]] = []

    provider_specs: List[Tuple[str, str, Any]] = []
    if "qwen35" in args.providers:
        provider_specs.append(("qwen35", "Qwen3.5-4B", args.qwen35_path))
    if "qwen06" in args.providers:
        provider_specs.append(("qwen06", "Qwen3-0.6B-SFT", args.qwen06_path))
    if "deepseek" in args.providers:
        api_key = os.environ.get("DEEPSEEK_API_KEY")
        if api_key:
            provider_specs.append(("deepseek", "deepseek-chat/deepseek-reasoner", api_key))
        else:
            print("[skip] DEEPSEEK_API_KEY 未设置，跳过 DeepSeek API")

    print("=" * 60)
    print("Pure LLM Benchmark")
    print("=" * 60)
    print(f"sample: {ground_truth['sample_id']}")
    print(f"raw_text_chars: {len(raw_text)}")
    print(f"gt_entities: {len(ground_truth['entities'])}")
    print(f"gt_relations: {len(ground_truth['relations'])}")
    print("-" * 60)

    for provider, model_name, provider_arg in provider_specs:
        client = None
        try:
            client = build_provider_client(provider, provider_arg, args)
            for task in args.tasks:
                for thinking_enabled in [False, True]:
                    print(f"[{provider}] task={task} thinking={thinking_enabled}")
                    result = run_single_task(
                        sample_id=ground_truth["sample_id"],
                        raw_text=raw_text,
                        ground_truth=ground_truth,
                        client=client,
                        provider=provider,
                        model_name=model_name,
                        task=task,
                        thinking_enabled=thinking_enabled,
                        max_new_tokens=args.max_new_tokens,
                    )
                    results.append(result)
                    print(
                        f"  status={result['status']} parse={result['parse_success']} "
                        f"f1={result['f1']:.4f} latency={result['latency_ms']:.2f}ms items={result['item_count']}"
                    )
                    if result["error"]:
                        print(f"  error={result['error']}")
        except Exception as exc:
            error = str(exc)
            print(f"[skip] {provider} 初始化失败: {error}")
            for task in args.tasks:
                for thinking_enabled in [False, True]:
                    results.append(
                        build_failed_result(
                            sample_id=ground_truth["sample_id"],
                            provider=provider,
                            model_name=model_name,
                            task=task,
                            thinking_enabled=thinking_enabled,
                            error=error,
                        )
                    )
        finally:
            if client is not None:
                del client
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()

    report = {
        "benchmark_type": "pure_llm_extraction",
        "sample_id": args.sample_id,
        "constraints": {
            "input_source": "raw_mineru_v2_linear_text_only",
            "disabled_components": [
                "phase1_block_classification",
                "phase1_section_scope_inheritance",
                "phase2_symbolic_preprocessor",
                "phase2_pre_annotations",
                "phase2_xml_tag_injection",
                "resume_prompts_rule_templates",
                "phase3_outlines_constrained_decode",
                "phase3_postprocessor",
                "output_adapter",
                "phase4_logic_guard",
                "feedback_loop",
            ],
            "entity_prompt": "neutral_json_only",
            "relation_prompt": "neutral_json_only_with_ground_truth_entities",
            "relation_entity_source": "ground_truth",
        },
        "ground_truth": {
            "sample_id": ground_truth["sample_id"],
            "entity_count": len(ground_truth["entities"]),
            "relation_count": len(ground_truth["relations"]),
        },
        "summary": summarize_results(results),
        "results": results,
    }

    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("-" * 60)
    print(f"output: {args.output}")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
