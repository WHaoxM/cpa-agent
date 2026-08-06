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

Use only the provided context (reports, portrait, target roles). If data is insufficient, still return valid JSON with empty arrays and growthScore 0, and explain insufficiency in summary — do not fabricate metrics.

Context:
{context_json}
