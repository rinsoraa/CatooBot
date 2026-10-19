# Default: pure contract tests, no Minecraft dependencies. -ServerDir: full build + all tests.
param([string]$ServerDir = '', [string]$JavaHome = $env:JAVA_HOME)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
if (-not $JavaHome) {
    $javac = Get-Command javac -ErrorAction SilentlyContinue
    if ($javac) { $JavaHome = Split-Path -Parent (Split-Path -Parent $javac.Source) }
}
if (-not $JavaHome) { throw 'Set JAVA_HOME or pass -JavaHome for JDK 21.' }
$javacPath = Join-Path $JavaHome 'bin/javac.exe'
$javaPath = Join-Path $JavaHome 'bin/java.exe'
foreach ($tool in @($javacPath, $javaPath)) {
    if (-not (Test-Path -LiteralPath $tool -PathType Leaf)) { throw "Missing JDK tool: $tool" }
}
$version = & $javacPath -version 2>&1
if ($LASTEXITCODE -ne 0 -or "$version" -notmatch '^javac 21\.') { throw "JDK 21 required; got: $version" }
$buildRoot = Join-Path $PSScriptRoot 'build'
New-Item -ItemType Directory -Path $buildRoot -Force | Out-Null
$output = Join-Path $buildRoot ('test-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $output | Out-Null
try {
    $testRoot = Join-Path $PSScriptRoot 'src/test/java/catoo/attrib'
    $mainRoot = Join-Path $PSScriptRoot 'src/main/java/catoo/attrib'
    $tests = @('BookingTest', 'DimensionPolicyTest', 'RequestStateTest', 'ShutdownTest', 'ProtocolTest')
    $sources = @('Booking.java', 'Hmac.java', 'Json.java', 'RequestState.java', 'RequestRegistry.java', 'ShadowResources.java') | ForEach-Object { Join-Path $mainRoot $_ }
    $classpath = $output
    if ($ServerDir) {
        & (Join-Path $PSScriptRoot 'build.ps1') -ServerDir $ServerDir -JavaHome $JavaHome
        $classpath = @((Join-Path $buildRoot 'catoobot-attribution-2.0.0.jar'), (Get-Content -LiteralPath (Join-Path $buildRoot 'classpath.txt') -Raw).Trim(), $output) -join [IO.Path]::PathSeparator
        $tests += @('EventJsonTest', 'ReserveBridgeTest', 'LifecycleBridgeTest', 'ProtocolBridgeTest', 'Smoke')
        $sources = @()
    }
    $sources += @($tests | ForEach-Object { Join-Path $testRoot ($_.ToString() + '.java') })
    & $javacPath --release 21 -encoding UTF-8 -proc:none -cp $classpath -d $output @sources
    if ($LASTEXITCODE -ne 0) { throw 'Java test compilation failed.' }
    foreach ($suite in $tests) {
        & $javaPath -ea -cp $classpath ('catoo.attrib.' + $suite)
        if ($LASTEXITCODE -ne 0) { throw "Java suite failed: $suite" }
    }
    'java_suites_passed=' + $tests.Count
} finally {
    $resolvedOutput = [IO.Path]::GetFullPath($output)
    $resolvedBuild = [IO.Path]::GetFullPath($buildRoot) + [IO.Path]::DirectorySeparatorChar
    if (-not $resolvedOutput.StartsWith($resolvedBuild, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Refusing test cleanup outside server_mod/build.'
    }
    if (Test-Path -LiteralPath $resolvedOutput) { Remove-Item -LiteralPath $resolvedOutput -Recurse -Force }
}
