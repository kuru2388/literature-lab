"""Planner agent: deep-understand the idea, then generate accurate search keywords."""

from __future__ import annotations

from src.llm import chat_json


def plan_research(idea: str, *, datasets_only: bool = False) -> dict:
    # ── Step 1: Deep understanding ────────────────────────────────────────────
    # Ask the LLM to decompose the idea before generating any keywords.
    # This prevents the "I don't really understand what you want" problem.
    try:
        understanding = chat_json(
            system=(
                "You are a research librarian. Your job is to deeply understand a student's "
                "project idea and break it into precise components that will drive accurate "
                "academic paper searches. Be specific — generic terms like 'machine learning' "
                "or 'deep learning' are useless as search terms."
            ),
            user=(
                f"Project idea:\n{idea}\n\n"
                "Analyse this idea and return JSON:\n"
                "{\n"
                '  "title": "concise project title (max 10 words)",\n'
                '  "core_task": "the specific ML/AI task (e.g. named entity recognition, anomaly detection, image segmentation)",\n'
                '  "application_domain": "specific application area (e.g. clinical notes, satellite imagery, financial transactions)",\n'
                '  "method_family": "likely algorithm family (e.g. transformer, graph neural network, contrastive learning)",\n'
                '  "key_constraints": "notable constraints or requirements (e.g. low-resource, real-time, federated)",\n'
                '  "likely_datasets": ["2-3 types of datasets or benchmarks commonly used for this task"],\n'
                '  "adjacent_fields": ["2-3 related research areas this work touches"],\n'
                '  "research_questions": ["4 to 6 specific research questions this project must answer"]\n'
                "}\n"
                "Be specific. Avoid vague terms."
            ),
        )
    except Exception:
        understanding = {}
    if not isinstance(understanding, dict):
        understanding = {}

    # ── Step 2: Generate keyword clusters from structured understanding ───────
    core_task = str(understanding.get("core_task") or "").strip()
    domain = str(understanding.get("application_domain") or "").strip()
    method = str(understanding.get("method_family") or "").strip()
    constraints = str(understanding.get("key_constraints") or "").strip()
    datasets = understanding.get("likely_datasets") or []
    adjacent = understanding.get("adjacent_fields") or []

    # Build a rich, structured context for the keyword generator
    context_lines = [f"Project idea:\n{idea}"]
    if core_task:
        context_lines.append(f"Core task: {core_task}")
    if domain:
        context_lines.append(f"Application domain: {domain}")
    if method:
        context_lines.append(f"Method family: {method}")
    if constraints:
        context_lines.append(f"Key constraints: {constraints}")
    if datasets:
        context_lines.append(f"Dataset types: {', '.join(str(d) for d in datasets)}")
    if adjacent:
        context_lines.append(f"Adjacent fields: {', '.join(str(a) for a in adjacent)}")
    context = "\n".join(context_lines)

    # When datasets_only, frame the search around dataset papers specifically
    if datasets_only:
        dataset_system = (
            "You generate precise search keywords to find academic papers that INTRODUCE, "
            "DESCRIBE, or BENCHMARK a dataset for a given research task. "
            "Rules:\n"
            "- Each keyword must be 2-5 words found in paper TITLES.\n"
            "- Focus on: dataset names, benchmark names, corpus names, annotation schemes, "
            "data collection methods, evaluation splits, and dataset construction.\n"
            "- Include terms like 'dataset', 'benchmark', 'corpus', 'annotation', "
            "'labeled data', 'ground truth', 'crowdsourcing', 'data augmentation'.\n"
            "- Do NOT include generic terms like 'machine learning', 'deep learning', 'survey'.\n"
            "- Include acronym variants (e.g. 'named entity recognition' AND 'NER dataset')."
        )
        dataset_user = (
            f"{context}\n\n"
            "IMPORTANT: I want papers about DATASETS for this task — papers that create, "
            "describe, release, or benchmark a dataset.\n\n"
            "Generate dataset-focused search keywords in JSON:\n"
            "{\n"
            '  "title": "short project title",\n'
            '  "cluster_task_domain": ["3-4 keywords: task + domain + dataset"],\n'
            '  "cluster_method": ["2-3 keywords: annotation method or data collection"],\n'
            '  "cluster_evaluation": ["3-4 keywords: benchmark names, evaluation datasets"],\n'
            '  "cluster_dataset_names": ["3-4 likely dataset/corpus/benchmark name patterns for this domain"],\n'
            '  "cluster_application": ["2-3 keywords: data release, labeled corpus, ground truth"],\n'
            '  "tags": ["all unique keywords merged, dataset-focused, best first, 10 to 16 total"]\n'
            "}\n"
            "Good example tags: 'clinical NER dataset', 'biomedical named entity corpus', "
            "'annotation benchmark', 'EHR labeled dataset', 'medical entity annotation', "
            "'dataset construction NLP', 'biomedical corpus annotation'."
        )
        try:
            data = chat_json(system=dataset_system, user=dataset_user)
        except Exception:
            data = {}
    else:
        try:
            data = chat_json(
                system=(
                    "You generate precise arXiv/PubMed/Semantic Scholar search keywords for a research project. "
                    "Rules:\n"
                    "- Each keyword must be 2-5 words, exactly the kind of phrase found in academic paper TITLES.\n"
                    "- Include acronym variants: if you add 'convolutional neural network', also add 'CNN'.\n"
                    "- Cover four keyword clusters: (1) task+domain, (2) method+technique, "
                    "(3) evaluation+dataset, (4) application+system.\n"
                    "- Do NOT include single words or generic terms like 'machine learning', 'deep learning', 'AI'.\n"
                    "- Prefer specific technical phrases that narrow results, not broaden them.\n"
                    "- If the idea has a Task, Domain, and Constraint, every keyword must stay within that scope."
                ),
                user=(
                    f"{context}\n\n"
                    "Generate search keywords in JSON:\n"
                    "{\n"
                    '  "title": "short project title",\n'
                    '  "cluster_task_domain": ["3-4 keywords combining the specific task and domain"],\n'
                    '  "cluster_method": ["3-4 keywords about the likely methods and models"],\n'
                    '  "cluster_evaluation": ["2-3 keywords about benchmarks, datasets, or metrics"],\n'
                    '  "cluster_application": ["2-3 keywords about the system or application angle"],\n'
                    '  "tags": ["all unique keywords merged, best first, 8 to 14 total"]\n'
                    "}\n"
                    "Good example tags: 'clinical named entity recognition', 'BioBERT fine-tuning', "
                    "'medical NER benchmark', 'EHR information extraction', 'transformer sequence labeling'."
                ),
            )
        except Exception:
            data = {}
    if not isinstance(data, dict):
        data = {}

    # Merge all cluster keywords into the tags list (deduplicated)
    all_tags: list[str] = []
    seen: set[str] = set()
    cluster_keys = ("tags", "cluster_task_domain", "cluster_method", "cluster_evaluation", "cluster_application")
    if datasets_only:
        cluster_keys = cluster_keys + ("cluster_dataset_names",)
    for cluster_key in cluster_keys:
        for item in data.get(cluster_key) or []:
            tag = _clean_tag(item)
            if tag and tag.lower() not in seen:
                seen.add(tag.lower())
                all_tags.append(tag)

    tag_cap = 16 if datasets_only else 14
    tags = all_tags[:tag_cap]
    if not tags:
        tags = _fallback_tags(idea)

    questions = [
        str(q).strip()
        for q in understanding.get("research_questions") or []
        if str(q).strip()
    ]

    return {
        "title": str(
            data.get("title")
            or understanding.get("title")
            or idea[:80]
        ),
        "tags": tags,
        "queries": tags,
        "research_questions": questions[:6],
        # Store the understanding for downstream agents
        "understanding": {
            "core_task": core_task,
            "application_domain": domain,
            "method_family": method,
            "key_constraints": constraints,
        },
    }


def _clean_tag(item: object) -> str:
    tag = " ".join(str(item).replace('"', "").replace("'", "").split())
    tag = tag.strip(" ,.;:")
    if not tag or len(tag) > 80 or len(tag.split()) < 1:
        return ""
    return tag


def _clean_tags(raw: list) -> list[str]:
    tags: list[str] = []
    seen: set[str] = set()
    for item in raw:
        tag = _clean_tag(item)
        if not tag:
            continue
        key = tag.lower()
        if key in seen:
            continue
        seen.add(key)
        tags.append(tag)
    return tags


def _fallback_tags(idea: str) -> list[str]:
    stop = {
        "the", "a", "an", "for", "and", "of", "to", "in", "on", "with", "using",
        "my", "our", "this", "that", "from", "into", "about", "build", "create",
        "want", "need", "make", "develop", "research", "project", "work",
    }
    words = [w for w in "".join(ch if ch.isalnum() or ch in "- " else " " for ch in idea).split() if w]
    words = [w for w in words if w.lower() not in stop]
    tags = []
    if len(words) >= 2:
        tags.append(" ".join(words[:3]))
    if len(words) >= 4:
        tags.append(" ".join(words[2:5]))
    if len(words) >= 6:
        tags.append(" ".join(words[4:7]))
    tags.extend(words[:6])
    return _clean_tags(tags)
