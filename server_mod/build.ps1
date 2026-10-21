# Compile against the fixed, read-only Fabric test-server installation. No deployment.
param(
    [string]$ServerDir = 'D:\Minecraft server\1.21.1',
    [string]$JavaHome = $env:JAVA_HOME
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$modRoot = $PSScriptRoot
if (-not $JavaHome) {
    $javac = Get-Command javac -ErrorAction SilentlyContinue
    if ($javac) { $JavaHome = Split-Path -Parent (Split-Path -Parent $javac.Source) }
}
if (-not $JavaHome) { throw 'Set JAVA_HOME or pass -JavaHome for JDK 21.' }
$javacPath = Join-Path $JavaHome 'bin/javac.exe'
$jarPath = Join-Path $JavaHome 'bin/jar.exe'
foreach ($tool in @($javacPath, $jarPath)) {
    if (-not (Test-Path -LiteralPath $tool -PathType Leaf)) { throw "Missing JDK tool: $tool" }
}
$version = & $javacPath -version 2>&1
if ($LASTEXITCODE -ne 0 -or "$version" -notmatch '^javac 21\.') {
    throw "JDK 21 required; got: $version"
}
$serverJar = Join-Path $ServerDir '.fabric/remappedJars/minecraft-1.21.1-0.19.5/server-intermediary.jar'
$loaderJar = Join-Path $ServerDir 'libraries/net/fabricmc/fabric-loader/0.19.5/fabric-loader-0.19.5.jar'
$mixinJar = @(Get-ChildItem -LiteralPath (Join-Path $ServerDir 'libraries/net/fabricmc/sponge-mixin') `
    -Recurse -Filter 'sponge-mixin-*.jar')
$apiJar = Join-Path $ServerDir 'mods/fabric-api-0.116.17+1.21.1.jar'
$brigadier = @(Get-ChildItem -LiteralPath (Join-Path $ServerDir 'libraries/com/mojang/brigadier') -Recurse -Filter '*.jar')
$dataFixer = @(Get-ChildItem -LiteralPath (Join-Path $ServerDir 'libraries/com/mojang/datafixerupper') `
    -Recurse -Filter 'datafixerupper-*.jar')
if ($brigadier.Count -ne 1) { throw 'Expected exactly one Brigadier JAR.' }
if ($dataFixer.Count -ne 1) { throw 'Expected exactly one DataFixerUpper JAR.' }
if ($mixinJar.Count -ne 1) { throw 'Expected exactly one Sponge Mixin JAR.' }
foreach ($file in @($serverJar, $loaderJar, $apiJar)) {
    if (-not (Test-Path -LiteralPath $file -PathType Leaf)) { throw "Missing pinned dependency: $file" }
}
$buildRoot = Join-Path $modRoot 'build'
New-Item -ItemType Directory -Path $buildRoot -Force | Out-Null
$stage = Join-Path $buildRoot ('stage-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $stage | Out-Null
try {
    $unpack = Join-Path $stage 'api'
    New-Item -ItemType Directory -Path $unpack | Out-Null
    Push-Location $unpack
    try {
        & $jarPath xf $apiJar
        if ($LASTEXITCODE -ne 0) { throw 'Fabric API extraction failed.' }
    } finally { Pop-Location }
    $nested = @(Get-ChildItem -LiteralPath (Join-Path $unpack 'META-INF/jars') -Filter '*.jar')
    $parts = foreach ($module in @('fabric-api-base', 'fabric-events-interaction-v0', 'fabric-networking-api-v1', 'fabric-lifecycle-events-v1')) {
        $matches = @($nested | Where-Object { $_.Name -like ($module + '-*') })
        if ($matches.Count -ne 1) { throw "Expected exactly one Fabric module: $module" }
        $matches[0].FullName
    }
    # Keep dependency extraction for the complete local test entry point.
    $dependencyDir = Join-Path $buildRoot 'deps'
    New-Item -ItemType Directory -Path $dependencyDir -Force | Out-Null
    $dependencyPaths = foreach ($part in $parts) {
        $destination = Join-Path $dependencyDir (Split-Path -Leaf $part)
        Copy-Item -LiteralPath $part -Destination $destination -Force
        $destination
    }
    $mixinExtras = Join-Path $dependencyDir 'mixinextras-fabric-0.5.5.jar'
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $extrasArchive = [IO.Compression.ZipFile]::OpenRead($loaderJar)
    try {
        $entry = $extrasArchive.GetEntry('META-INF/jars/mixinextras-fabric-0.5.5.jar')
        if (-not $entry) { throw 'Pinned MixinExtras entry missing from Fabric Loader.' }
        [IO.Compression.ZipFileExtensions]::ExtractToFile($entry, $mixinExtras, $true)
    } finally { $extrasArchive.Dispose() }
    $classpath = (@($serverJar, $loaderJar, $mixinJar[0].FullName, $brigadier[0].FullName,
        $dataFixer[0].FullName, $mixinExtras) `
        + @($dependencyPaths)) -join [IO.Path]::PathSeparator
    $classes = Join-Path $stage 'classes'
    New-Item -ItemType Directory -Path $classes | Out-Null
    $sources = @(Get-ChildItem -LiteralPath (Join-Path $modRoot 'src/main/java') -Recurse -Filter '*.java' | Sort-Object FullName | ForEach-Object { $_.FullName })
    & $javacPath --release 21 -nowarn -proc:none -encoding UTF-8 -cp $classpath -d $classes @sources
    if ($LASTEXITCODE -ne 0) { throw 'Java compilation failed; no new JAR was packaged.' }
    Copy-Item -LiteralPath (Join-Path $modRoot 'fabric.mod.json') -Destination (Join-Path $classes 'fabric.mod.json')
    $resources = Join-Path $modRoot 'src/main/resources'
    if (Test-Path -LiteralPath $resources) {
        Copy-Item -Path (Join-Path $resources '*') -Destination $classes -Recurse -Force
    }
    $candidate = Join-Path $stage 'candidate.jar'
    Push-Location $classes
    try {
        # Fixed ZIP timestamps make identical sources/dependencies produce identical JAR bytes.
        & $jarPath --create --file $candidate --date=2026-10-18T00:00:00Z .
        if ($LASTEXITCODE -ne 0) { throw 'JAR packaging failed.' }
    } finally { Pop-Location }
    $sha = [Security.Cryptography.SHA256]::Create()
    $stream = [IO.File]::OpenRead($candidate)
    try { $digest = [BitConverter]::ToString($sha.ComputeHash($stream)).Replace('-', '') }
    finally { $stream.Dispose(); $sha.Dispose() }
    Set-Content -LiteralPath (Join-Path $buildRoot 'classpath.txt') -Value $classpath -Encoding UTF8
    $artifact = Join-Path $buildRoot 'catoobot-attribution-2.0.0.jar'
    Copy-Item -LiteralPath $candidate -Destination $artifact -Force
    'jar=' + $artifact
    'jar_sha256=' + $digest
} finally {
    # Only delete this invocation's verified staging directory inside server_mod/build.
    $resolvedStage = [IO.Path]::GetFullPath($stage)
    $resolvedBuild = [IO.Path]::GetFullPath($buildRoot) + [IO.Path]::DirectorySeparatorChar
    if (-not $resolvedStage.StartsWith($resolvedBuild, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Refusing staging cleanup outside server_mod/build.'
    }
    if (Test-Path -LiteralPath $resolvedStage) { Remove-Item -LiteralPath $resolvedStage -Recurse -Force }
}
