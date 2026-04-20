<#
.SYNOPSIS
    Deprovisions a NemoClaw bot mailbox pair for a single user.

.DESCRIPTION
    Undoes everything Provision-NemoClawUser.ps1 creates, in reverse order:
      - Mail flow rules (inbound + outbound)
      - RBAC role assignments
      - Management scopes (bot + user)
      - Exchange service principal
      - Entra app registration (+ all its secrets)
      - Shared bot mailbox

    Idempotent: skips resources that don't exist.

    By default, runs in dry-run mode and only reports what would be deleted.
    Pass -Confirm to actually delete.

.PARAMETER Username
    The user's mailbox local part (e.g. "jsmith" for jsmith@contoso.com).

.PARAMETER Domain
    The tenant mail domain (e.g. "contoso.com" or "contoso.onmicrosoft.com").

.PARAMETER Confirm
    Actually perform deletions. Without this switch, only reports.

.PARAMETER KeepMailbox
    Preserve the shared bot mailbox (and its contents) even when deleting
    everything else. Useful if you want to retain message history.

.PARAMETER RealName
    Optional override for the user's display name (e.g. "Jane Smith"). Used
    to find the app registration whose name matches "NemoClaw Bot - <RealName>".
    If not provided, the script reads the DisplayName from the user's mailbox.
    Only needed if the user mailbox has been deleted or renamed since
    provisioning.

.EXAMPLE
    # Dry run - show what would be deleted
    .\Deprovision-NemoClawUser.ps1 -Username jsmith -Domain contoso.com

.EXAMPLE
    # Actually deprovision
    .\Deprovision-NemoClawUser.ps1 -Username jsmith -Domain contoso.com -Confirm

.EXAMPLE
    # Deprovision but keep the bot mailbox for archival
    .\Deprovision-NemoClawUser.ps1 -Username jsmith -Domain contoso.com -Confirm -KeepMailbox

.EXAMPLE
    # Deprovision when user mailbox is gone - provide the name explicitly
    .\Deprovision-NemoClawUser.ps1 -Username jsmith -Domain contoso.com -RealName "Jane Smith" -Confirm

.NOTES
    Requires modules: ExchangeOnlineManagement, Microsoft.Graph.Applications
    Caller must be a member of Organization Management in Exchange Online
    and have Application.ReadWrite.All + Directory.ReadWrite.All in Graph.
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory)] [string] $Username,
    [Parameter(Mandatory)] [string] $Domain,
    [switch] $Confirm,
    [switch] $KeepMailbox,
    [string] $RealName
)

$ErrorActionPreference = 'Stop'

# Match the naming conventions from the provisioning script
$UserMailbox   = "$Username@$Domain"
$BotMailbox    = "$Username-bot@$Domain"
# AppName is resolved after we determine RealName below
$BotScopeName  = "$Username Bot Scope"
$UserScopeName = "$Username User Scope"
$BotRoleRW     = "$Username Bot ReadWrite"
$BotRoleSend   = "$Username Bot Send"
$UserRoleRead  = "$Username User Read"
$OutboundRule  = "NemoClaw Outbound - $Username"
$InboundRule   = "NemoClaw Inbound - $Username"

$Mode = if ($Confirm) { "LIVE" } else { "DRY RUN" }
Write-Host "=== Deprovisioning NemoClaw bot for $UserMailbox ($Mode) ===" -ForegroundColor Cyan
Write-Host "Bot mailbox: $BotMailbox`n" -ForegroundColor Cyan

if (-not $Confirm) {
    Write-Host "*** DRY RUN - no changes will be made. Pass -Confirm to actually deprovision. ***`n" -ForegroundColor Yellow
}

# ----------------------------------------------------------------------------
# Connect
# ----------------------------------------------------------------------------
if (-not (Get-ConnectionInformation -ErrorAction SilentlyContinue)) {
    Write-Host "Connecting to Exchange Online..." -ForegroundColor Yellow
    Connect-ExchangeOnline -ShowBanner:$false
}
if (-not (Get-MgContext -ErrorAction SilentlyContinue)) {
    Write-Host "Connecting to Microsoft Graph..." -ForegroundColor Yellow
    Connect-MgGraph -Scopes 'Application.ReadWrite.All','Directory.ReadWrite.All' -NoWelcome
}

# ----------------------------------------------------------------------------
# Resolve app name: try RealName from param, then user mailbox DisplayName,
# then fall back to matching any app that starts with "NemoClaw Bot -" and
# has the username embedded in its associated resources.
# ----------------------------------------------------------------------------
if (-not $RealName) {
    try {
        $userMailboxObj = Get-Mailbox -Identity $UserMailbox -ErrorAction Stop
        $RealName = $userMailboxObj.DisplayName
        if ($RealName) {
            Write-Host "[OK] Using real name from mailbox: $RealName"
        }
    } catch {
        Write-Host "[WARN] User mailbox $UserMailbox not found - will search for app by pattern" -ForegroundColor Yellow
    }
}

if ($RealName) {
    $AppName = "NemoClaw Bot - $RealName"
    Write-Host "[OK] Looking for app: '$AppName'"
} else {
    # Last resort: find the app by looking for one whose Exchange service
    # principal is referenced in this user's role assignments.
    $AppName = $null
    $existingAssignment = Get-ManagementRoleAssignment -Identity $BotRoleRW -ErrorAction SilentlyContinue
    if ($existingAssignment) {
        $spObjectId = $existingAssignment.RoleAssigneeName
        $fallbackApp = Get-MgApplication -Filter "startsWith(displayName,'NemoClaw Bot -')" -ErrorAction SilentlyContinue |
                       Where-Object { (Get-MgServicePrincipal -Filter "appId eq '$($_.AppId)'").Id -eq $spObjectId } |
                       Select-Object -First 1
        if ($fallbackApp) {
            $AppName = $fallbackApp.DisplayName
            Write-Host "[OK] Found app by role-assignment lookup: '$AppName'"
        }
    }
    if (-not $AppName) {
        Write-Host "[WARN] Could not resolve app name. Pass -RealName explicitly to clean up the app registration." -ForegroundColor Yellow
    }
}

function Invoke-Delete {
    param(
        [string] $Label,
        [scriptblock] $Action
    )
    if ($Confirm) {
        Write-Host "[DELETE] $Label" -ForegroundColor Red
        & $Action
    } else {
        Write-Host "[WOULD DELETE] $Label" -ForegroundColor Yellow
    }
}

# ----------------------------------------------------------------------------
# 1. Mail flow rules (safest to remove first - they gate traffic)
# ----------------------------------------------------------------------------
Write-Host "--- Mail flow rules ---"
if (Get-TransportRule -Identity $OutboundRule -ErrorAction SilentlyContinue) {
    Invoke-Delete -Label "Outbound rule '$OutboundRule'" -Action {
        Remove-TransportRule -Identity $OutboundRule -Confirm:$false
    }
} else {
    Write-Host "[SKIP] Outbound rule '$OutboundRule' not found"
}

if (Get-TransportRule -Identity $InboundRule -ErrorAction SilentlyContinue) {
    Invoke-Delete -Label "Inbound rule '$InboundRule'" -Action {
        Remove-TransportRule -Identity $InboundRule -Confirm:$false
    }
} else {
    Write-Host "[SKIP] Inbound rule '$InboundRule' not found"
}

# ----------------------------------------------------------------------------
# 2. RBAC role assignments
# ----------------------------------------------------------------------------
Write-Host "`n--- RBAC role assignments ---"
foreach ($roleName in @($BotRoleRW, $BotRoleSend, $UserRoleRead)) {
    if (Get-ManagementRoleAssignment -Identity $roleName -ErrorAction SilentlyContinue) {
        Invoke-Delete -Label "Role assignment '$roleName'" -Action {
            Remove-ManagementRoleAssignment -Identity $roleName -Confirm:$false
        }
    } else {
        Write-Host "[SKIP] Role assignment '$roleName' not found"
    }
}

# ----------------------------------------------------------------------------
# 3. Management scopes
# ----------------------------------------------------------------------------
Write-Host "`n--- Management scopes ---"
foreach ($scopeName in @($BotScopeName, $UserScopeName)) {
    if (Get-ManagementScope -Identity $scopeName -ErrorAction SilentlyContinue) {
        Invoke-Delete -Label "Scope '$scopeName'" -Action {
            Remove-ManagementScope -Identity $scopeName -Confirm:$false
        }
    } else {
        Write-Host "[SKIP] Scope '$scopeName' not found"
    }
}

# ----------------------------------------------------------------------------
# 4. Exchange service principal
# ----------------------------------------------------------------------------
Write-Host "`n--- Exchange service principal ---"
if ($AppName) {
    $app = Get-MgApplication -Filter "displayName eq '$AppName'" -ErrorAction SilentlyContinue | Select-Object -First 1
} else {
    $app = $null
}
if ($app) {
    $exoSp = Get-ServicePrincipal -Identity $app.AppId -ErrorAction SilentlyContinue
    if ($exoSp) {
        Invoke-Delete -Label "Exchange service principal (AppId=$($app.AppId))" -Action {
            Remove-ServicePrincipal -Identity $exoSp.Identity -Confirm:$false
        }
    } else {
        Write-Host "[SKIP] Exchange service principal not found"
    }
} else {
    $appLabel = if ($AppName) { "'$AppName'" } else { "(unresolved)" }
    Write-Host "[SKIP] App $appLabel not found - no Exchange SP to remove"
}

# ----------------------------------------------------------------------------
# 5. Entra app registration (removes all secrets automatically)
# ----------------------------------------------------------------------------
Write-Host "`n--- Entra app registration ---"
if ($app) {
    # Also clean up the enterprise app (service principal in Entra)
    $entraSp = Get-MgServicePrincipal -Filter "appId eq '$($app.AppId)'" -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($entraSp) {
        Invoke-Delete -Label "Enterprise application (Entra SP for $($app.AppId))" -Action {
            Remove-MgServicePrincipal -ServicePrincipalId $entraSp.Id
        }
    }

    Invoke-Delete -Label "App registration '$AppName' (AppId=$($app.AppId))" -Action {
        Remove-MgApplication -ApplicationId $app.Id
    }
} else {
    $appLabel = if ($AppName) { "'$AppName'" } else { "(unresolved)" }
    Write-Host "[SKIP] App registration $appLabel not found"
}

# ----------------------------------------------------------------------------
# 6. Shared bot mailbox (last - irreversible data loss)
# ----------------------------------------------------------------------------
Write-Host "`n--- Shared bot mailbox ---"
if ($KeepMailbox) {
    Write-Host "[KEEP] Preserving mailbox $BotMailbox (-KeepMailbox specified)" -ForegroundColor Yellow
} elseif (Get-Mailbox -Identity $BotMailbox -ErrorAction SilentlyContinue) {
    Invoke-Delete -Label "Shared mailbox $BotMailbox (ALL MESSAGES WILL BE LOST)" -Action {
        Remove-Mailbox -Identity $BotMailbox -Confirm:$false
    }
} else {
    Write-Host "[SKIP] Shared mailbox $BotMailbox not found"
}

# ----------------------------------------------------------------------------
# Summary
# ----------------------------------------------------------------------------
Write-Host "`n=== Done ===" -ForegroundColor Cyan
if (-not $Confirm) {
    Write-Host "This was a DRY RUN. Re-run with -Confirm to actually deprovision." -ForegroundColor Yellow
} else {
    Write-Host "Deprovisioning complete for $UserMailbox." -ForegroundColor Green
    Write-Host "Remember to:" -ForegroundColor Yellow
    Write-Host "  - Remove bridge service credentials from the user's config/secret store"
    Write-Host "  - Stop any running bridge instances for this user"
    if ($KeepMailbox) {
        Write-Host "  - The bot mailbox $BotMailbox was preserved (-KeepMailbox)"
    }
}