<#
.SYNOPSIS
    Provisions a NemoClaw bot mailbox pair for a single user.

.DESCRIPTION
    Creates the full set of resources required for one user to run the
    NemoClaw/OpenShell Outlook bridge:
      - Shared bot mailbox (<username>-bot@<domain>)
      - Entra app registration + client secret
      - Exchange service principal
      - Two management scopes (bot + user) filtering on SMTP address
      - Three RBAC role assignments (Mail.ReadWrite + Mail.Send on bot,
        Mail.Read on user)
      - Two mail flow rules pairing the bot to the user

    Idempotent: re-running skips resources that already exist.

.PARAMETER Username
    The user's mailbox local part (e.g. "jsmith" for jsmith@contoso.com).

.PARAMETER Domain
    The tenant mail domain (e.g. "contoso.com" or "contoso.onmicrosoft.com").

.PARAMETER RealName
    Optional override for the user's display name (e.g. "Jane Smith"). If not
    provided, the script reads the DisplayName from the user's mailbox.
    The bot mailbox will be named "<RealName> Bot" so it appears naturally
    in recipients' inboxes (e.g. "Jane Smith Bot <jsmith-bot@contoso.com>").

.EXAMPLE
    .\Provision-NemoClawUser.ps1 -Username jsmith -Domain contoso.com

.EXAMPLE
    .\Provision-NemoClawUser.ps1 -Username jsmith -Domain contoso.com -RealName "Jane Smith"

.NOTES
    Requires modules: ExchangeOnlineManagement, Microsoft.Graph.Applications
    Caller must be a member of Organization Management in Exchange Online
    and have Application.ReadWrite.All + Directory.ReadWrite.All in Graph.
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory)] [string] $Username,
    [Parameter(Mandatory)] [string] $Domain,
    [string] $RealName
)

$ErrorActionPreference = 'Stop'

$UserMailbox  = "$Username@$Domain"
$BotMailbox   = "$Username-bot@$Domain"
# AppName is finalized after we resolve RealName from the user mailbox below
$BotScopeName  = "$Username Bot Scope"
$UserScopeName = "$Username User Scope"
$BotRoleRW    = "$Username Bot ReadWrite"
$BotRoleSend  = "$Username Bot Send"
$UserRoleRead = "$Username User Read"
$OutboundRule = "NemoClaw Outbound - $Username"
$InboundRule  = "NemoClaw Inbound - $Username"

Write-Host "=== Provisioning NemoClaw bot for $UserMailbox ===" -ForegroundColor Cyan
Write-Host "Bot mailbox: $BotMailbox`n" -ForegroundColor Cyan

# ----------------------------------------------------------------------------
# 1. Connect (assumes caller has already loaded modules)
# ----------------------------------------------------------------------------
if (-not (Get-ConnectionInformation -ErrorAction SilentlyContinue)) {
    Write-Host "Connecting to Exchange Online..." -ForegroundColor Yellow
    Connect-ExchangeOnline -ShowBanner:$false
}
if (-not (Get-MgContext -ErrorAction SilentlyContinue)) {
    Write-Host "Connecting to Microsoft Graph..." -ForegroundColor Yellow
    Connect-MgGraph -Scopes 'Application.ReadWrite.All','Directory.ReadWrite.All' -NoWelcome
}

# Verify the user mailbox exists and grab its display name for the bot's name
try {
    $userMailboxObj = Get-Mailbox -Identity $UserMailbox -ErrorAction Stop
    Write-Host "[OK] User mailbox $UserMailbox exists (DisplayName: $($userMailboxObj.DisplayName))"
} catch {
    throw "User mailbox $UserMailbox not found. Create the user first."
}

if (-not $RealName) {
    $RealName = $userMailboxObj.DisplayName
    if (-not $RealName) {
        throw "User mailbox has no DisplayName and no -RealName was provided."
    }
    Write-Host "[OK] Using real name from mailbox: $RealName"
}

$BotDisplayName = "$RealName Bot"                   # e.g. "Jane Smith Bot"
$AppName        = "NemoClaw Bot - $RealName"        # e.g. "NemoClaw Bot - Jane Smith"

# ----------------------------------------------------------------------------
# 2. Shared bot mailbox
# ----------------------------------------------------------------------------
if (Get-Mailbox -Identity $BotMailbox -ErrorAction SilentlyContinue) {
    Write-Host "[SKIP] Shared mailbox $BotMailbox already exists"
} else {
    Write-Host "[CREATE] Shared mailbox $BotMailbox (DisplayName: $BotDisplayName)"
    New-Mailbox -Shared -Name $BotDisplayName -DisplayName $BotDisplayName `
                -Alias "$Username-bot" -PrimarySmtpAddress $BotMailbox | Out-Null
}

# ----------------------------------------------------------------------------
# 3. Entra app registration + client secret
# ----------------------------------------------------------------------------
$app = Get-MgApplication -Filter "displayName eq '$AppName'" -ErrorAction SilentlyContinue | Select-Object -First 1
if ($app) {
    Write-Host "[SKIP] App registration '$AppName' already exists (AppId=$($app.AppId))"
} else {
    Write-Host "[CREATE] App registration '$AppName'"
    $app = New-MgApplication -DisplayName $AppName -SignInAudience 'AzureADMyOrg'
}

Write-Host "[CREATE] Client secret (24 months)"
$secret = Add-MgApplicationPassword -ApplicationId $app.Id `
    -PasswordCredential @{ displayName = "Provisioning $(Get-Date -Format yyyy-MM-dd)"; endDateTime = (Get-Date).AddMonths(24) }

# Ensure service principal exists in Entra (enterprise app)
$sp = Get-MgServicePrincipal -Filter "appId eq '$($app.AppId)'" -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $sp) {
    Write-Host "[CREATE] Enterprise application (service principal)"
    $sp = New-MgServicePrincipal -AppId $app.AppId
}

# ----------------------------------------------------------------------------
# 4. Exchange service principal
# ----------------------------------------------------------------------------
# Newly-created Entra service principals can take 30s-2min to become visible
# to Exchange Online, so we retry with a backoff.
if (Get-ServicePrincipal -Identity $app.AppId -ErrorAction SilentlyContinue) {
    Write-Host "[SKIP] Exchange service principal already exists"
} else {
    Write-Host "[CREATE] Exchange service principal (may take a moment to sync from Entra)"
    $maxAttempts = 12   # ~4 minutes total
    $attempt = 0
    $created = $false
    while (-not $created -and $attempt -lt $maxAttempts) {
        $attempt++
        try {
            New-ServicePrincipal -AppId $app.AppId -ObjectId $sp.Id -DisplayName $AppName -ErrorAction Stop | Out-Null
            $created = $true
            Write-Host "[OK] Exchange service principal created on attempt $attempt"
        } catch {
            if ($_.Exception.Message -match 'AADServicePrincipalNotFound') {
                Write-Host "  Waiting for Entra sync (attempt $attempt/$maxAttempts)..." -ForegroundColor Yellow
                Start-Sleep -Seconds 20
            } else {
                throw
            }
        }
    }
    if (-not $created) {
        throw "Exchange service principal creation failed after $maxAttempts attempts. Entra sync may be delayed; try running the script again in a few minutes."
    }
}

# ----------------------------------------------------------------------------
# 5. Management scopes (direct SMTP address filters)
# ----------------------------------------------------------------------------
if (Get-ManagementScope -Identity $BotScopeName -ErrorAction SilentlyContinue) {
    Write-Host "[SKIP] Scope '$BotScopeName' exists"
} else {
    Write-Host "[CREATE] Scope '$BotScopeName'"
    New-ManagementScope -Name $BotScopeName `
        -RecipientRestrictionFilter "PrimarySmtpAddress -eq '$BotMailbox'" | Out-Null
}

if (Get-ManagementScope -Identity $UserScopeName -ErrorAction SilentlyContinue) {
    Write-Host "[SKIP] Scope '$UserScopeName' exists"
} else {
    Write-Host "[CREATE] Scope '$UserScopeName'"
    New-ManagementScope -Name $UserScopeName `
        -RecipientRestrictionFilter "PrimarySmtpAddress -eq '$UserMailbox'" | Out-Null
}

# ----------------------------------------------------------------------------
# 6. RBAC role assignments
# ----------------------------------------------------------------------------
function Set-RoleAssignment {
    param([string]$Name, [string]$Role, [string]$Scope)
    if (Get-ManagementRoleAssignment -Identity $Name -ErrorAction SilentlyContinue) {
        Write-Host "[SKIP] Role assignment '$Name' exists"
    } else {
        Write-Host "[CREATE] Role assignment '$Name' ($Role -> $Scope)"
        New-ManagementRoleAssignment -Name $Name -App $app.AppId `
            -Role $Role -CustomResourceScope $Scope | Out-Null
    }
}

Set-RoleAssignment -Name $BotRoleRW   -Role 'Application Mail.ReadWrite' -Scope $BotScopeName
Set-RoleAssignment -Name $BotRoleSend -Role 'Application Mail.Send'      -Scope $BotScopeName
Set-RoleAssignment -Name $UserRoleRead -Role 'Application Mail.Read'     -Scope $UserScopeName

# ----------------------------------------------------------------------------
# 7. Mail flow rules (bot <-> user pairing)
# ----------------------------------------------------------------------------
if (Get-TransportRule -Identity $OutboundRule -ErrorAction SilentlyContinue) {
    Write-Host "[SKIP] Outbound rule '$OutboundRule' exists"
} else {
    Write-Host "[CREATE] Outbound rule '$OutboundRule'"
    New-TransportRule -Name $OutboundRule `
        -From $BotMailbox -ExceptIfSentTo $UserMailbox `
        -RejectMessageReasonText "NemoClaw bot mailbox can only send to its paired user." `
        -Mode Enforce | Out-Null
}

if (Get-TransportRule -Identity $InboundRule -ErrorAction SilentlyContinue) {
    Write-Host "[SKIP] Inbound rule '$InboundRule' exists"
} else {
    Write-Host "[CREATE] Inbound rule '$InboundRule'"
    New-TransportRule -Name $InboundRule `
        -SentTo $BotMailbox -ExceptIfFrom $UserMailbox `
        -RejectMessageReasonText "NemoClaw bot mailbox only accepts mail from its paired user." `
        -Mode Enforce | Out-Null
}

# ----------------------------------------------------------------------------
# Verification
# ----------------------------------------------------------------------------
Write-Host "`n=== Verification ===" -ForegroundColor Cyan
Write-Host "Bot mailbox permissions:" -ForegroundColor Yellow
Test-ServicePrincipalAuthorization -Identity $app.AppId -Resource $BotMailbox |
    Format-Table RoleName, InScope -AutoSize

Write-Host "User mailbox permissions:" -ForegroundColor Yellow
Test-ServicePrincipalAuthorization -Identity $app.AppId -Resource $UserMailbox |
    Format-Table RoleName, InScope -AutoSize

# ----------------------------------------------------------------------------
# Output credentials for bridge service config
# ----------------------------------------------------------------------------
$tenantId = (Get-MgContext).TenantId

Write-Host "`n=== Credentials for outlook-bridge ===" -ForegroundColor Green
Write-Host "Store these securely and provide to the user:`n"
Write-Host "OUTLOOK_TENANT_ID=$tenantId"
Write-Host "OUTLOOK_CLIENT_ID=$($app.AppId)"
Write-Host "OUTLOOK_CLIENT_SECRET=$($secret.SecretText)"
Write-Host "OUTLOOK_BOT_MAILBOX=$BotMailbox"
Write-Host "OUTLOOK_USER_MAILBOX=$UserMailbox"
Write-Host ""
Write-Host "Client secret will not be retrievable again." -ForegroundColor Yellow
Write-Host "Secret expires: $($secret.EndDateTime)" -ForegroundColor Yellow