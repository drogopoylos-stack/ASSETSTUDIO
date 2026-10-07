# Creates a Desktop + Start-Menu shortcut to the silent Asset Studio launcher, with the
# app icon AND a fixed AppUserModelID. The AUMID must match the one the Electron app sets
# (app.setAppUserModelId("AssetStudio")) so Windows groups the running window UNDER this
# shortcut — that's what makes "Pin to taskbar" work and relaunch the full app instead of
# spawning a separate, broken electron.exe button.
#   Run once:  right-click -> Run with PowerShell.  Then PIN THE SHORTCUT (not the running app).
$ErrorActionPreference = "Stop"
$root  = $PSScriptRoot
$vbs   = Join-Path $root "Asset Studio.vbs"
$icon  = Join-Path $root "frontend\electron\icon.ico"
$AUMID = "AssetStudio"

if (-not (Test-Path $vbs)) { Write-Host "Launcher not found: $vbs" -ForegroundColor Red; exit 1 }

# --- helper: stamp a fixed AppUserModelID onto a .lnk (Windows property store interop) ---
$cs = @"
using System;
using System.Runtime.InteropServices;
namespace LnkAumid {
  [StructLayout(LayoutKind.Sequential, Pack=4)]
  public struct PropertyKey { public Guid fmtid; public uint pid;
    public PropertyKey(Guid f, uint p){ fmtid=f; pid=p; } }
  [StructLayout(LayoutKind.Sequential)]
  public struct PropVariant { public ushort vt; ushort r1, r2, r3; public IntPtr p; public IntPtr p2; }
  [ComImport, Guid("886d8eeb-8cf2-4446-8d02-cdba1dbdcf99"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
  public interface IPropertyStore {
    void GetCount(out uint c); void GetAt(uint i, out PropertyKey pk);
    void GetValue(ref PropertyKey k, out PropVariant pv);
    void SetValue(ref PropertyKey k, ref PropVariant pv); void Commit(); }
  [ComImport, Guid("0000010b-0000-0000-C000-000000000046"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
  public interface IPersistFile {
    void GetClassID(out Guid g); [PreserveSig] int IsDirty();
    void Load([MarshalAs(UnmanagedType.LPWStr)] string f, int mode);
    void Save([MarshalAs(UnmanagedType.LPWStr)] string f, [MarshalAs(UnmanagedType.Bool)] bool remember);
    void SaveCompleted([MarshalAs(UnmanagedType.LPWStr)] string f);
    void GetCurFile([MarshalAs(UnmanagedType.LPWStr)] out string f); }
  [ComImport, Guid("00021401-0000-0000-C000-000000000046")] public class ShellLink {}
  public static class Setter {
    [DllImport("ole32.dll")] static extern int PropVariantClear(ref PropVariant pv);
    public static void Set(string lnk, string aumid) {
      var pf = (IPersistFile)new ShellLink();
      pf.Load(lnk, 2);                               // STGM_READWRITE
      var ps = (IPropertyStore)pf;
      var key = new PropertyKey(new Guid("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3"), 5); // System.AppUserModel.ID
      var pv = new PropVariant();
      pv.vt = 31;                                    // VT_LPWSTR
      pv.p = System.Runtime.InteropServices.Marshal.StringToCoTaskMemUni(aumid);
      ps.SetValue(ref key, ref pv); ps.Commit();
      PropVariantClear(ref pv);                      // frees the string we allocated
      pf.Save(lnk, true);
    } } }
"@
try { Add-Type -TypeDefinition $cs -Language CSharp } catch { }

$ws = New-Object -ComObject WScript.Shell
$targets = @(
    [Environment]::GetFolderPath('Desktop'),
    (Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs')
)
foreach ($dir in $targets) {
    $lnk = Join-Path $dir 'Asset Studio.lnk'
    $sc  = $ws.CreateShortcut($lnk)
    $sc.TargetPath       = "wscript.exe"
    $sc.Arguments        = '"' + $vbs + '"'
    $sc.WorkingDirectory = $root
    if (Test-Path $icon) { $sc.IconLocation = $icon }
    $sc.Description       = "Asset Studio"
    $sc.Save()
    # stamp the matching AppUserModelID so the taskbar can pin + group it correctly
    try { [LnkAumid.Setter]::Set($lnk, $AUMID); Write-Host "Created (pinnable): $lnk" -ForegroundColor Green }
    catch { Write-Host "Created: $lnk  (AppID stamp skipped: $($_.Exception.Message))" -ForegroundColor Yellow }
}
Write-Host ""
Write-Host "Done. To pin: right-click 'Asset Studio' (Desktop or Start) -> Pin to taskbar." -ForegroundColor Cyan
Write-Host "Pin the SHORTCUT (not the running window). Closing the app keeps it in the tray; the pin relaunches it." -ForegroundColor DarkGray
