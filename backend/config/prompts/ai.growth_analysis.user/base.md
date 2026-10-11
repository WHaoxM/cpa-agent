# Growth analysis — return one JSON object matching the frontend AIAnalysisResult schema.

Required keys (all required):
- generatedAt (ISO string)
- summary (string)
- growthScore (number 0-100)
- scoreTooltip (string)
- topAbilities (string[])
- topAbilityDetails (list of objects with name and detail)
- improvementAreas (string[])
- improvementDetails (list of objects with name and detail)
- careerSuggestions (string[])
- timeline (list of objects with date and label)
- nextSteps (string[])

Use only the provided, owner-verified context (reports, published portrait, target roles and source_refs). The server will set growthScore and scoreTooltip from the published portrait after generation; do not invent or recalculate a numeric score. If a qualitative claim lacks evidence, omit that claim rather than filling it with a guessed value. Never treat browser snapshots or an incomplete task as an authoritative report.

Context:
{context_json}
