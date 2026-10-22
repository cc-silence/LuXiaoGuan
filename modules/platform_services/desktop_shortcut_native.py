"""Unicode Windows shell-link interfaces compiled only in the isolated shell worker."""

SHELL_LINK_SOURCE = r'''
using System;
using System.Text;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Runtime.InteropServices.ComTypes;

[ComImport, Guid("00021401-0000-0000-C000-000000000046")]
class RouterShellLinkObject { }

[ComImport, Guid("000214F9-0000-0000-C000-000000000046"),
 InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IRouterShellLinkW {
    void GetPath([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder path, int count, IntPtr data, int flags);
    void GetIDList(out IntPtr list);
    void SetIDList(IntPtr list);
    void GetDescription([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder text, int count);
    void SetDescription([MarshalAs(UnmanagedType.LPWStr)] string text);
    void GetWorkingDirectory([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder path, int count);
    void SetWorkingDirectory([MarshalAs(UnmanagedType.LPWStr)] string path);
    void GetArguments([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder text, int count);
    void SetArguments([MarshalAs(UnmanagedType.LPWStr)] string text);
    void GetHotkey(out short hotkey);
    void SetHotkey(short hotkey);
    void GetShowCmd(out int command);
    void SetShowCmd(int command);
    void GetIconLocation([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder path, int count, out int index);
    void SetIconLocation([MarshalAs(UnmanagedType.LPWStr)] string path, int index);
    void SetRelativePath([MarshalAs(UnmanagedType.LPWStr)] string path, int reserved);
    void Resolve(IntPtr window, int flags);
    void SetPath([MarshalAs(UnmanagedType.LPWStr)] string path);
}

public static class RouterShellLinkUnicode {
    const int Capacity = 32768;
    public static Dictionary<string, object> Read(string path) {
        object instance = new RouterShellLinkObject();
        try {
            ((IPersistFile)instance).Load(path, 0);
            var link = (IRouterShellLinkW)instance;
            var value = new StringBuilder(Capacity);
            var result = new Dictionary<string, object>();
            link.GetPath(value, Capacity, IntPtr.Zero, 4);
            result["target"] = value.ToString();
            value.Clear(); link.GetArguments(value, Capacity);
            result["arguments"] = value.ToString();
            value.Clear(); link.GetWorkingDirectory(value, Capacity);
            result["working_directory"] = value.ToString();
            value.Clear(); int index; link.GetIconLocation(value, Capacity, out index);
            result["icon"] = value.ToString() + "," + index;
            value.Clear(); link.GetDescription(value, Capacity);
            result["description"] = value.ToString();
            return result;
        } finally { Marshal.FinalReleaseComObject(instance); }
    }
    public static void Save(string path, string target, string arguments,
                            string directory, string icon, string description) {
        object instance = new RouterShellLinkObject();
        try {
            var link = (IRouterShellLinkW)instance;
            link.SetPath(target);
            link.SetArguments(arguments);
            link.SetWorkingDirectory(directory);
            int separator = icon.LastIndexOf(',');
            link.SetIconLocation(icon.Substring(0, separator),
                                 Int32.Parse(icon.Substring(separator + 1)));
            link.SetDescription(description);
            link.SetShowCmd(1);
            ((IPersistFile)instance).Save(path, true);
        } finally { Marshal.FinalReleaseComObject(instance); }
    }
}
'''
