param(
    [string]$Root = "D:\Windows内容\文档\deepseek-harness\default-workspace\zip2epub"
)

# 通过 WMI 创建进程：脱离当前作业对象，方便测试结束后独立清理
$cmd = 'cmd /c "set TEMP=' + $Root + '\.tmp&& set TMP=' + $Root + '\.tmp&& cd /d ' + $Root + '&& dist\zip2epub.exe"'
$result = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{ CommandLine = $cmd }
"pid=$($result.ProcessId) rc=$($result.ReturnValue)"
