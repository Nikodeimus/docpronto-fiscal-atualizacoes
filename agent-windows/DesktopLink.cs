using System.IO.Pipes;
using System.Security.Principal;
using System.Security.Cryptography;
using System.Text;
using Microsoft.Win32;
using System.Diagnostics;

static class DesktopLink
{
    internal static string Pipe => "DocPronto-"+WindowsIdentity.GetCurrent().User!.Value.Replace('-','_');
    internal static string? Initial;
    internal static bool Send(string message)
    {
        try{using var pipe=new NamedPipeClientStream(".",Pipe,PipeDirection.Out,PipeOptions.CurrentUserOnly);pipe.Connect(2000);using var writer=new StreamWriter(pipe);writer.WriteLine(message);return true;}catch{return false;}
    }
    internal static async Task Listen(AgentWindow window)
    {
        while(!window.IsDisposed)
        {
            try{using var pipe=new NamedPipeServerStream(Pipe,PipeDirection.In,1,PipeTransmissionMode.Byte,PipeOptions.Asynchronous|PipeOptions.CurrentUserOnly);
                await pipe.WaitForConnectionAsync();var bytes=new byte[8192];int total=0;
                while(total<bytes.Length){int n=await pipe.ReadAsync(bytes.AsMemory(total),new CancellationTokenSource(3000).Token);if(n==0)break;total+=n;if(Array.IndexOf(bytes,(byte)10,0,total)>=0)break;}
                var message=Encoding.UTF8.GetString(bytes,0,total).Trim();window.BeginInvoke(()=>window.HandleLink(message));
            }catch{await Task.Delay(500);}
        }
    }
    internal static Dictionary<string,string> Parse(string value)
    {
        if(value.Length>4096||!Uri.TryCreate(value,UriKind.Absolute,out var uri)||uri.Scheme!="docpronto"||uri.UserInfo.Length!=0||uri.Port!=-1||uri.Fragment.Length!=0||uri.AbsolutePath is not ("" or "/"))throw new AgentError("Link do DocPronto inválido.");
        if(uri.Host is not ("open" or "pair" or "update"))throw new AgentError("Ação não reconhecida.");
        var result=new Dictionary<string,string>{{"action",uri.Host}};
        foreach(var part in uri.Query.TrimStart('?').Split('&',StringSplitOptions.RemoveEmptyEntries)){
            var p=part.Split('=',2);if(p.Length!=2||p[0] is not ("server" or "code")||!result.TryAdd(p[0],Uri.UnescapeDataString(p[1])))throw new AgentError("Parâmetro inválido.");}
        if(uri.Host=="pair"){
            if(!result.TryGetValue("server",out var server)||!result.TryGetValue("code",out var code)||code.Length!=16||code.Any(c=>!Uri.IsHexDigit(c)))throw new AgentError("Código de conexão inválido.");
            ConnectedAgent.ValidateServer(server);
        }else if(result.Count!=1)throw new AgentError("Parâmetros não permitidos.");
        return result;
    }
    internal static void Install()
    {
        ApplicationConfiguration.Initialize();
        if(MessageBox.Show("Instalar o conector DocPronto neste usuário e iniciar com o Windows? Não é necessário abrir terminal.","DocPronto",MessageBoxButtons.OKCancel)!=DialogResult.OK)return;
        try{
            var source=Environment.ProcessPath!;var hash=Convert.ToHexString(SHA256.HashData(File.ReadAllBytes(source)))[..16];
            var folder=Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),"DocPronto","agent-versions",hash);Directory.CreateDirectory(folder);
            var target=Path.Combine(folder,"DocPronto.CertificateAgent.exe");if(!File.Exists(target))File.Copy(source,target);
            using(var key=Registry.CurrentUser.CreateSubKey(@"Software\Classes\docpronto")){
                key.SetValue("","URL:DocPronto");key.SetValue("URL Protocol","");using var command=key.CreateSubKey(@"shell\open\command");command.SetValue("","\""+target+"\" \"%1\"");}
            using(var key=Registry.CurrentUser.CreateSubKey(@"Software\Microsoft\Windows\CurrentVersion\Run"))key.SetValue("DocProntoAgent","\""+target+"\" --background");
            using(var key=Registry.CurrentUser.CreateSubKey(@"Software\DocPronto"))key.SetValue("AgentPath",target);
            Send("exit");
            // Only replace the user's previous official installation, never unrelated processes.
            var legacy=Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),"DocPronto","bin","DocPronto.CertificateAgent.exe");
            foreach(var process in Process.GetProcessesByName("DocPronto.CertificateAgent")){
                try{if(process.Id!=Environment.ProcessId&&string.Equals(process.MainModule?.FileName,legacy,StringComparison.OrdinalIgnoreCase))process.Kill(true);}catch{}finally{process.Dispose();}}
            var launch=new ProcessStartInfo(target){UseShellExecute=true};launch.ArgumentList.Add("--wait-desktop");Process.Start(launch);
            MessageBox.Show("Conector instalado. Volte ao site e clique em Conectar computador. Nas próximas utilizações ele inicia com o Windows.","DocPronto");
        }catch{MessageBox.Show("Não foi possível instalar o conector. Confira a permissão da pasta do usuário.","DocPronto");}
    }
}
