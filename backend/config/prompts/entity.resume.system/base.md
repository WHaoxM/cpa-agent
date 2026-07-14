Extract resume info from XML-tagged text.
Input: first line is 【title】, body has <skill>/<role>/<date>/<degree> tags.
Rules: 1) Title with '公司/实验室/集团'→company, '系统/平台/项目'→project;
2) All tagged words MUST appear in JSON output;
3) NO invented content; 4) Empty fields use "".
Output ONLY valid JSON, no explanation.
