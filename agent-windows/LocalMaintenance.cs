using System.IO.Compression;
using System.Diagnostics;

static class LocalMaintenance
{
    internal static void Update(Form owner)
    {
        using var picker=new OpenFileDialog{Title="Selecione o pacote oficial de atualização DocPronto",Filter="Pacote DocPronto (*.zip)|*.zip"};
        if(picker.ShowDialog(owner)!=DialogResult.OK)return;
        if(MessageBox.Show(owner,"O pacote selecionado contém programas que serão executados neste computador. Continue apenas com um pacote DocPronto recebido de uma fonte confiável. A atualização fará backup antes de trocar a versão.","Atualizar DocPronto",MessageBoxButtons.OKCancel)!=DialogResult.OK)return;
        try{
            using var archive=ZipFile.OpenRead(picker.FileName);
            if(archive.Entries.Count>3000||archive.Entries.Sum(e=>e.Length)>1024L*1024*1024)throw new AgentError("Pacote maior que o permitido.");
            var root=Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),"DocPronto","releases",Guid.NewGuid().ToString("N"));
            foreach(var entry in archive.Entries){
                var name=entry.FullName.Replace('\\','/');
                if(!name.StartsWith("DocPronto/",StringComparison.Ordinal)||name.Contains(':')||name.Split('/').Any(p=>p is ".." or ".")||Path.IsPathRooted(name))throw new AgentError("Estrutura de pacote inválida.");
            }
            if(archive.GetEntry("DocPronto/scripts/setup-window.ps1") is null||archive.GetEntry("DocPronto/compose.yaml") is null)throw new AgentError("Use um pacote com o instalador guiado (1.7 ou posterior).");
            Directory.CreateDirectory(root);archive.ExtractToDirectory(root);
            Launch(Path.Combine(root,"DocPronto","scripts","setup-window.ps1"));
        }catch(Exception ex){MessageBox.Show(owner,ex is AgentError?ex.Message:"Não foi possível abrir o pacote de atualização.","DocPronto");}
    }
    internal static void Launch(string script)
    {
        var process=new ProcessStartInfo("powershell.exe"){UseShellExecute=false,CreateNoWindow=true};
        foreach(var arg in new[]{"-NoProfile","-ExecutionPolicy","Bypass","-WindowStyle","Hidden","-File",script})process.ArgumentList.Add(arg);
        Process.Start(process);
    }
}
