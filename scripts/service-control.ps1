function Open-PlatformControlLock([string]$Path) {
    $deadline=(Get-Date).AddSeconds(5)
    do {
        try {return [System.IO.File]::Open($Path,[System.IO.FileMode]::OpenOrCreate,[System.IO.FileAccess]::ReadWrite,[System.IO.FileShare]::None)}
        catch [System.IO.IOException] {
            if((Get-Date) -ge $deadline){throw '另一项服务启停操作尚未结束，请稍后重试。'}
            Start-Sleep -Milliseconds 50
        }
    } while($true)
}
