# ============================================================
# Jobyn AI v2 - Production API End-to-End Test
# ============================================================

$BaseUrl = "https://jobyn-ai-v2.vercel.app"
$Api = "$BaseUrl/api/v1"

Write-Host ""
Write-Host "=============================================" -ForegroundColor Cyan
Write-Host "       JOBYN AI v2 PRODUCTION TEST" -ForegroundColor Cyan
Write-Host "=============================================" -ForegroundColor Cyan
Write-Host ""

# ------------------------------------------------------------
# Helpers
# ------------------------------------------------------------

function Test-Endpoint {
    param(
        [string]$Name,
        [string]$Method,
        [string]$Url,
        [hashtable]$Headers = @{},
        [string]$Body = $null
    )

    Write-Host ""
    Write-Host "TEST: $Name" -ForegroundColor Yellow
    Write-Host "$Method $Url" -ForegroundColor DarkGray

    try {
        if ($Body) {
            $response = Invoke-RestMethod `
                -Uri $Url `
                -Method $Method `
                -Headers $Headers `
                -ContentType "application/json" `
                -Body $Body `
                -ErrorAction Stop
        }
        else {
            $response = Invoke-RestMethod `
                -Uri $Url `
                -Method $Method `
                -Headers $Headers `
                -ErrorAction Stop
        }

        Write-Host "PASS" -ForegroundColor Green

        return $response
    }
    catch {
        Write-Host "FAIL" -ForegroundColor Red
        Write-Host $_.Exception.Message -ForegroundColor Red

        if ($_.ErrorDetails.Message) {
            Write-Host $_.ErrorDetails.Message -ForegroundColor DarkRed
        }

        return $null
    }
}

# ============================================================
# 1. HEALTH
# ============================================================

$health = Test-Endpoint `
    -Name "Health Check" `
    -Method "GET" `
    -Url "$Api/health"

$health | ConvertTo-Json -Depth 10

# ============================================================
# 2. READINESS
# ============================================================

$ready = Test-Endpoint `
    -Name "Readiness Check" `
    -Method "GET" `
    -Url "$Api/health/ready"

$ready | ConvertTo-Json -Depth 10

# ============================================================
# 3. LLM HEALTH
# ============================================================

$llm = Test-Endpoint `
    -Name "LLM Health Check" `
    -Method "GET" `
    -Url "$Api/health/llm"

$llm | ConvertTo-Json -Depth 10

# ============================================================
# 4. REGISTER
# ============================================================

$timestamp = Get-Date -Format "yyyyMMddHHmmss"

$Email = "jobyn.test.$timestamp@example.com"
$Password = "JobynTest123!"
$FullName = "Jobyn Production Tester"

$registerBody = @{
    email = $Email
    password = $Password
    full_name = $FullName
} | ConvertTo-Json

$register = Test-Endpoint `
    -Name "User Registration" `
    -Method "POST" `
    -Url "$Api/auth/register" `
    -Body $registerBody

$register | ConvertTo-Json -Depth 10

# ============================================================
# 5. LOGIN
# ============================================================

$loginBody = @{
    email = $Email
    password = $Password
} | ConvertTo-Json

$login = Test-Endpoint `
    -Name "User Login" `
    -Method "POST" `
    -Url "$Api/auth/login" `
    -Body $loginBody

$login | ConvertTo-Json -Depth 10

if (-not $login.access_token) {
    Write-Host ""
    Write-Host "LOGIN FAILED - stopping authenticated tests." -ForegroundColor Red
    exit 1
}

$Token = $login.access_token

Write-Host ""
Write-Host "JWT received successfully." -ForegroundColor Green

$AuthHeaders = @{
    Authorization = "Bearer $Token"
}

# ============================================================
# 6. AUTHENTICATED USER
# ============================================================

$me = Test-Endpoint `
    -Name "Authenticated User (/auth/me)" `
    -Method "GET" `
    -Url "$Api/auth/me" `
    -Headers $AuthHeaders

$me | ConvertTo-Json -Depth 10

# ============================================================
# 7. USERS/ME
# ============================================================

$userMe = Test-Endpoint `
    -Name "Authenticated User (/users/me)" `
    -Method "GET" `
    -Url "$Api/users/me" `
    -Headers $AuthHeaders

$userMe | ConvertTo-Json -Depth 10

# ============================================================
# 8. JOB LIST
# ============================================================

$jobs = Test-Endpoint `
    -Name "List Jobs" `
    -Method "GET" `
    -Url "$Api/jobs" `
    -Headers $AuthHeaders

$jobs | ConvertTo-Json -Depth 10

# ============================================================
# SUMMARY
# ============================================================

Write-Host ""
Write-Host "=============================================" -ForegroundColor Cyan
Write-Host "        INITIAL API TEST COMPLETE" -ForegroundColor Cyan
Write-Host "=============================================" -ForegroundColor Cyan
Write-Host ""

Write-Host "Test account:" -ForegroundColor Yellow
Write-Host $Email

Write-Host ""
Write-Host "Next tests:" -ForegroundColor Yellow
Write-Host "  1. Upload CV"
Write-Host "  2. Parse CV"
Write-Host "  3. Candidate profile"
Write-Host "  4. Job ingestion"
Write-Host "  5. Deterministic matching"
Write-Host "  6. AI matching"
Write-Host "  7. Career Coach"
Write-Host "  8. Application Copilot"
Write-Host "  9. Interview preparation"