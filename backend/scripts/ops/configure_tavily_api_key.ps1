$ErrorActionPreference = 'Stop'

$repoRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..\..'))
$envPath = Join-Path $repoRoot '.env'
$secureValue = $null
$secretValue = $null
$buffer = [IntPtr]::Zero

try {
    if (-not (Test-Path -LiteralPath $envPath -PathType Leaf)) {
        throw '项目根目录 .env 不存在；未创建文件。'
    }

    & git -C $repoRoot check-ignore --quiet -- .env
    if ($LASTEXITCODE -ne 0) {
        throw '项目根目录 .env 未被 Git 忽略；未读取密钥。'
    }

    & git -C $repoRoot ls-files --error-unmatch -- .env *> $null
    if ($LASTEXITCODE -eq 0) {
        throw '项目根目录 .env 已被 Git 跟踪；未读取密钥。'
    }

    $content = [System.IO.File]::ReadAllText($envPath, [System.Text.Encoding]::UTF8)
    $pattern = '(?m)^(?:\s*#\s*)?TAVILY_API_KEY\s*=.*$'
    $existing = [System.Text.RegularExpressions.Regex]::Matches($content, $pattern)
    if ($existing.Count -gt 1) {
        throw '根目录 .env 中存在多个 Tavily 配置行；未读取密钥。'
    }

    $secureValue = Read-Host '请输入 Tavily key（输入不回显；不会出现在命令历史中）' -AsSecureString
    if (-not $secureValue -or $secureValue.Length -eq 0) {
        throw '没有输入密钥；配置未修改。'
    }

    $buffer = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureValue)
    $secretValue = [System.Runtime.InteropServices.Marshal]::PtrToStringBSTR($buffer)
    if ($secretValue -match '[\s\0]') {
        throw '输入含空白或控制字符；配置未修改。'
    }

    $escapedValue = $secretValue.Replace('\', '\\').Replace('"', '\"')
    $newline = if ($content.Contains("`r`n")) { "`r`n" } else { "`n" }
    $newLineText = 'TAVILY_API_KEY="' + $escapedValue + '"'

    if ($existing.Count -eq 1) {
        $match = $existing[0]
        $content = $content.Substring(0, $match.Index) + $newLineText + $content.Substring($match.Index + $match.Length)
    } else {
        if ($content.Length -gt 0 -and -not $content.EndsWith("`n")) {
            $content += $newline
        }
        $content += $newLineText + $newline
    }

    [System.IO.File]::WriteAllText($envPath, $content, (New-Object System.Text.UTF8Encoding($false)))
    Write-Output 'TAVILY_API_KEY 已写入被 Git 忽略的项目根 .env；密钥值未显示。重启后端后才会加载。'
} catch {
    Write-Error ('配置未完成：' + $_.Exception.Message)
    exit 1
} finally {
    if ($buffer -ne [IntPtr]::Zero) {
        [System.Runtime.InteropServices.Marshal]::ZeroFreeBSTR($buffer)
    }
    if ($secureValue) {
        $secureValue.Dispose()
    }
    $secretValue = $null
    $escapedValue = $null
}
