$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"

if (Test-Path -LiteralPath $venvPython) {
    $python = $venvPython
} else {
    $pythonCommand = Get-Command py -ErrorAction SilentlyContinue
    if (-not $pythonCommand) {
        $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    }
    if (-not $pythonCommand) {
        throw "Python не найден. Установите Python 3.11+ или создайте .venv в папке проекта."
    }
    $python = $pythonCommand.Source
}

Push-Location $projectRoot
try {
    Write-Host "Установка зависимостей..."
    & $python -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) { throw "Не удалось установить зависимости." }

    Write-Host "Сборка QueueTula.exe..."
    & $python -m PyInstaller --clean --noconfirm queue_system.spec
    if ($LASTEXITCODE -ne 0) { throw "Сборка EXE завершилась с ошибкой." }
} finally {
    Pop-Location
}

Write-Host "Готово: $projectRoot\dist\QueueTula.exe"
Write-Host "При первом запуске рядом с EXE автоматически создастся папка data."
