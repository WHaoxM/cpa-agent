"""Public failure explanations, never raw exceptions or successful outcomes."""


def failure_reply(code):
    code = str(code or '')
    retained = '原问题和已保存的材料仍保留，不需要重新上传。'
    if code == 'task_discussion_capacity':
        return '这项任务积累的补充已超出目前可安全接续的范围，尚未生成新答复。' + retained + '系统没有静默删掉早先纠正，也没有自动压缩；请先核对历史并明确本次范围，不能把这次中断当作已经记住全部上下文。'
    if code == 'turn_token_cap_exceeded':
        return '本轮分析的调用额度不足，尚未形成完整答复。' + retained + '请缩小本次问题范围后再发送；没有生成新结论或计划。'
    if code.startswith('turn_token_usage_unverified') or code in {'turn_token_usage_unavailable','usage_ledger_unavailable'}:
        return '模型服务没有返回可核对的用量，这次分析已暂停；这不表示预算已经用完。' + retained + '请先核对进展，不会自动重复调用模型。'
    if code in {'estimated_input_over_window','estimated_input_over_limit','estimated_input_exceeds_window'}:
        return '本次资料超过模型可接收的窗口，尚未完成分析。' + retained + '请按章节或缩小范围继续，不能把当前结果当作全文结论。'
    if code in {'model_capability_unknown','tokenizer_unknown','input_estimate_unknown','multimodal_or_nontext_unknown'}:
        return '目前无法核对模型能接收的资料范围，这次分析没有继续。' + retained + '请先核对服务配置，不是简历已经用完预算。'
    if code.startswith('runtime_release_changed') or code.startswith('education_pipeline_release_changed'):
        return '分析服务在执行期间发生更新，这次结果未采用。' + retained + '服务稳定后可以重新发送本次问题。'
    if code in {'task_answer_validation_failed','task_terminal_answer_pending','task_empty_answer'}:
        return '助手这次没有整理出通过资料与格式核对的完整答复，不是文件上传失败。' + retained + '可以取回这次问题再试；没有生成新结论或计划。'
    if code in {'ValueError','TypeError','ValidationError','unconfirmed_resume_claim'}:
        return '这次资料读取或答复核对没有完成，尚未发布新结论。' + retained + '请先重新读取任务进展，核对资料后再发送；不是已经确认业务完成。'
    if code in {'TimeoutError','APITimeoutError','ReadTimeout','ConnectTimeout','ConnectionError','APIConnectionError'}:
        return '这次分析服务未能及时响应，尚未形成完整答复。' + retained + '请先核对已保存进展，再决定是否重试。'
    if code in {'tool_failed','tool_execution_error'}:
        return '这次所需资料没有读取完整，不能据此发布结论。' + retained + '请先核对资料可用状态，再继续；不会把未读内容当作已经读完。'
    if code == 'resource_read_result_unverified':
        return ('本次执行没有取得可核验的资料读取结果，因此系统没有把资料作为依据或发布结论。'
                '原问题和任务记录仍保留。请核对当前任务状态，或调整请求后再提交；系统不会自动重试。')
    return '这次分析未完成，尚未发布新结论或计划。' + retained + '请先核对当前进展；如果仍失败，请保留这次记录供排查，不必反复上传。'
