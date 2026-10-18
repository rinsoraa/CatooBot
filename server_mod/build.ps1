# P2 可复现构建（无 Gradle；直接用 JDK 21 对 intermediary 编译）
# 用法: pwsh -NoProfile -File server_mod/build.ps1
param([string]$ServerDir = 'D:\Minecraft server\1.21.1')
$ErrorActionPreference = 'Stop'
$mk = Split-Path -Parent $MyInvocation.MyCommand.Path
$jdk = 'C:\Program Files\Java\jdk-21\bin'
$srv = Join-Path $ServerDir '.fabric\remappedJars\minecraft-1.21.1-0.19.5\server-intermediary.jar'
$loaderJar = (Get-ChildItem (Join-Path $ServerDir 'libraries\net\fabricmc\fabric-loader') -Recurse -Filter 'fabric-loader-*.jar' | Select-Object -First 1).FullName
$apiJar = (Get-ChildItem (Join-Path $ServerDir 'mods') -Filter 'fabric-api-*.jar' | Select-Object -First 1).FullName
$brigJar = (Get-ChildItem (Join-Path $ServerDir 'libraries\com\mojang\brigadier') -Recurse -Filter '*.jar' | Select-Object -First 1).FullName
$tmp = Join-Path $env:TEMP ('catoo-api-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $tmp | Out-Null
Push-Location $tmp; & (Join-Path $jdk 'jar.exe') xf $apiJar; Pop-Location
$nested = Get-ChildItem (Join-Path $tmp 'META-INF\jars') -Filter '*.jar'
$need = @('fabric-api-base', 'fabric-events-interaction-v0', 'fabric-networking-api-v1')
$parts = foreach ($n in $need) { ($nested | Where-Object { $_.Name -like ($n + '-*') } | Select-Object -First 1).FullName }
$cp = (@($srv, $loaderJar, $brigJar) + $parts) -join ';'
$out = Join-Path $mk 'build\classes'
if (Test-Path $out) { Remove-Item -Recurse -Force $out }
New-Item -ItemType Directory -Path $out -Force | Out-Null
& (Join-Path $jdk 'javac.exe') -nowarn -proc:none -encoding UTF-8 -cp $cp -d $out (Get-ChildItem (Join-Path $mk 'src\main\java') -Recurse -Filter *.java | ForEach-Object { $_.FullName })
Copy-Item (Join-Path $mk 'fabric.mod.json') (Join-Path $out 'fabric.mod.json') -Force
$jar = Join-Path $mk 'build\catoobot-attribution-2.0.0.jar'
Push-Location $out; & (Join-Path $jdk 'jar.exe') --create --file $jar .; Pop-Location
"jar=" + $jar
"jar_sha256=" + (Get-FileHash $jar -Algorithm SHA256).Hash
Remove-Item -Recurse -Force $tmp
