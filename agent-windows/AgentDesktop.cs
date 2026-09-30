using System.Diagnostics;
using System.Text.Json;
using System.Security.Cryptography.X509Certificates;
using Microsoft.Win32;
using System.Runtime.InteropServices;

static class AgentDesktop
{
    [DllImport("kernel32.dll")] static extern bool FreeConsole();
    internal static void Open()
    {
        using var single=new Mutex(true,"Local\\DocProntoAgentDesktop",out bool first);
        if(!first){DesktopLink.Send(DesktopLink.Initial??"docpronto://open");return;}
        FreeConsole();ApplicationConfiguration.Initialize();Application.Run(new AgentWindow());
    }
}
sealed class AgentWindow:Form
{
    readonly TextBox url=new(){Text="http://localhost:8080"}, code=new(){UseSystemPasswordChar=true}, name=new(){Text=Environment.MachineName};
    readonly ListBox profiles=new();readonly TextBox log=new(){Multiline=true,ReadOnly=true,ScrollBars=ScrollBars.Vertical};
    readonly TextBox certificateSearch=new(){PlaceholderText="Buscar nome, CPF/CNPJ ou final do certificado",Dock=DockStyle.Top};
    readonly ListBox certificates=new(){Dock=DockStyle.Fill,HorizontalScrollbar=true};
    readonly ListBox bindings=new(){Dock=DockStyle.Fill,HorizontalScrollbar=true};
    readonly Label inventoryCount=new(){Dock=DockStyle.Bottom,Height=24};
    readonly Dictionary<string,AgentStatus> statuses=new();
    readonly Dictionary<string,Process> running=new();readonly NotifyIcon tray=new();
    readonly string directory=Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),"DocPronto","profiles");
    readonly List<string> files=new();bool quit;
    public AgentWindow()
    {
        Text="DocPronto • Conexão de certificados";Width=1050;Height=860;MinimumSize=new Size(760,740);
        var grid=new TableLayoutPanel{Dock=DockStyle.Fill,Padding=new Padding(24),ColumnCount=1,RowCount=12};Controls.Add(grid);
        grid.Controls.Add(new Label{Text="Conecte este computador uma vez. Troque a empresa e o certificado pelo site.",AutoSize=true});
        AddField(grid,"Endereço do DocPronto",url);AddField(grid,"Código de conexão gerado no site",code);AddField(grid,"Nome deste computador",name);
        var pair=new Button{Text="Conectar computador",AutoSize=true};grid.Controls.Add(pair);
        var tabs=new TabControl{Height=240,Dock=DockStyle.Fill};
        var connectionsPage=new TabPage("Conexões com o site");
        var certificatesPage=new TabPage("Certificados do Windows");
        var bindingsPage=new TabPage("Certificados por CNPJ/CPF");
        tabs.TabPages.AddRange(new[]{connectionsPage,certificatesPage,bindingsPage});
        profiles.Dock=DockStyle.Fill;connectionsPage.Controls.Add(profiles);
        connectionsPage.Controls.Add(new Label{Text="Estes são vínculos com o site. Veja os certificados nas outras abas.",Dock=DockStyle.Bottom,Height=24});
        certificatesPage.Controls.Add(certificates);certificatesPage.Controls.Add(certificateSearch);certificatesPage.Controls.Add(inventoryCount);
        bindingsPage.Controls.Add(bindings);
        bindingsPage.Controls.Add(new Label{Text="Escolha no site: Certificados → Usar nesta empresa. Cada consulta usa o vínculo do seu CNPJ/CPF.",Dock=DockStyle.Bottom,Height=32});
        grid.Controls.Add(tabs);
        profiles.SelectedIndexChanged+=(_,_)=>RenderCertificates();certificateSearch.TextChanged+=(_,_)=>RenderCertificates();
        var actions=new FlowLayoutPanel{AutoSize=true,Dock=DockStyle.Fill};grid.Controls.Add(actions);
        var start=new Button{Text="Retomar conexão",AutoSize=true};var stop=new Button{Text="Pausar conexão",AutoSize=true};actions.Controls.Add(start);actions.Controls.Add(stop);var forget=new Button{Text="Remover conexão antiga",AutoSize=true};actions.Controls.Add(forget);forget.Click+=(_,_)=>{if(profiles.SelectedIndex<0)return;var path=files[profiles.SelectedIndex];if(MessageBox.Show(this,"Remover somente esta conexão salva no computador? Cadastros e documentos do site serão preservados.","DocPronto",MessageBoxButtons.OKCancel)!=DialogResult.OK)return;Stop(path);File.Delete(path);RefreshProfiles();};
        var importA1=new Button{Text="Instalar certificado A1",AutoSize=true};actions.Controls.Add(importA1);importA1.Click+=(_,_)=>ImportA1();
        var startup=new CheckBox{Text="Abrir o agente ao entrar no Windows",AutoSize=true};grid.Controls.Add(startup);
        using(var key=Registry.CurrentUser.OpenSubKey(@"Software\Microsoft\Windows\CurrentVersion\Run")) startup.Checked=key?.GetValue("DocProntoAgent") is not null;
        startup.CheckedChanged+=(_,_)=>{try{using var key=Registry.CurrentUser.CreateSubKey(@"Software\Microsoft\Windows\CurrentVersion\Run");if(startup.Checked)key.SetValue("DocProntoAgent","\""+Environment.ProcessPath+"\" --background");else key.DeleteValue("DocProntoAgent",false);}catch(Exception){MessageBox.Show("Não foi possível configurar a inicialização.");}};
        log.Height=150;log.Dock=DockStyle.Fill;grid.Controls.Add(log);grid.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        pair.Click+=async(_,_)=>{pair.Enabled=false;try{Directory.CreateDirectory(directory);string path=Path.Combine(directory,Guid.NewGuid().ToString("N")+".json");await ConnectedAgent.PairValues(url.Text,code.Text,name.Text,"",path);code.Clear();RefreshProfiles();Start(path);Append("Computador conectado. No site, reutilize esta conexão nas outras empresas e escolha o certificado.");}catch(Exception ex){Append(ex is AgentError?ex.Message:"Falha ao conectar. Confira o endereço e gere novo código se expirou.");}finally{pair.Enabled=true;}};
        start.Click+=(_,_)=>{if(profiles.SelectedIndex>=0)Start(files[profiles.SelectedIndex]);};
        stop.Click+=(_,_)=>{if(profiles.SelectedIndex>=0)Stop(files[profiles.SelectedIndex]);};
        tray.Icon=SystemIcons.Application;tray.Text="DocPronto Agente";tray.Visible=true;
        var menu=new ContextMenuStrip();menu.Items.Add("Abrir",null,(_,_)=>{Show();WindowState=FormWindowState.Normal;Activate();});menu.Items.Add("Sair e desconectar",null,(_,_)=>{quit=true;Close();});tray.ContextMenuStrip=menu;tray.DoubleClick+=(_,_)=>{Show();Activate();};
        FormClosing+=(_,e)=>{if(!quit){e.Cancel=true;Hide();return;}foreach(var path in running.Keys.ToArray())Stop(path);tray.Dispose();};
        Shown+=(_,_)=>{_ = DesktopLink.Listen(this);if(DesktopLink.Initial is not null)HandleLink(DesktopLink.Initial);RefreshProfiles();foreach(var path in files.ToArray())Start(path);if(Environment.GetCommandLineArgs().Contains("--background"))Hide();};
    }
    internal void HandleLink(string message)
    {
        if(message=="exit"){quit=true;Close();return;}
        try{
            var data=DesktopLink.Parse(message);Show();WindowState=FormWindowState.Normal;Activate();
            if(data["action"]=="pair"){
                url.Text=data["server"];code.Text=data["code"];
                Append("Pedido recebido do site. Confira o endereço e clique em Conectar computador para autorizar.");
            }
            if(data["action"]=="update")LocalMaintenance.Update(this);
        }catch(AgentError ex){MessageBox.Show(this,ex.Message,"DocPronto");}
    }
    void ImportA1()
    {
        using var picker=new OpenFileDialog{Title="Selecione seu certificado A1",Filter="Certificado A1 (*.pfx;*.p12)|*.pfx;*.p12",CheckFileExists=true};
        if(picker.ShowDialog(this)!=DialogResult.OK)return;
        using var dialog=new Form{Text="Instalar certificado A1 neste usuário Windows",Width=480,Height=200,StartPosition=FormStartPosition.CenterParent,FormBorderStyle=FormBorderStyle.FixedDialog,MaximizeBox=false,MinimizeBox=false};
        var panel=new FlowLayoutPanel{Dock=DockStyle.Fill,Padding=new Padding(16),FlowDirection=FlowDirection.TopDown};
        panel.Controls.Add(new Label{Text="Senha do arquivo (usada somente neste computador)",AutoSize=true});
        var password=new TextBox{UseSystemPasswordChar=true,Width=420};panel.Controls.Add(password);
        var install=new Button{Text="Instalar no Windows",AutoSize=true,DialogResult=DialogResult.OK};panel.Controls.Add(install);dialog.Controls.Add(panel);dialog.AcceptButton=install;
        if(dialog.ShowDialog(this)!=DialogResult.OK){password.Clear();return;}
        try
        {
            var info=new FileInfo(picker.FileName);if(info.Length>10*1024*1024)throw new AgentError("Arquivo maior que 10 MB.");
            using var cert=X509CertificateLoader.LoadPkcs12FromFile(picker.FileName,password.Text,X509KeyStorageFlags.UserKeySet|X509KeyStorageFlags.PersistKeySet);
            if(!cert.HasPrivateKey)throw new AgentError("O arquivo não contém chave privada.");
            using var store=new X509Store(StoreName.My,StoreLocation.CurrentUser);store.Open(OpenFlags.ReadWrite);store.Add(cert);
            Append("Certificado instalado no Windows. Atualize os certificados no site e vincule à empresa.");
        }
        catch(Exception ex){MessageBox.Show(this,ex is AgentError?ex.Message:"Não foi possível importar. Confira o arquivo e a senha.","Certificado A1");}
        finally{password.Clear();}
    }
    static void AddField(TableLayoutPanel grid,string label,Control field){grid.Controls.Add(new Label{Text=label,AutoSize=true});field.Dock=DockStyle.Top;grid.Controls.Add(field);}
    void Append(string text){if(IsDisposed)return;if(InvokeRequired){BeginInvoke(()=>Append(text));return;}if(log.TextLength>24000)log.Clear();log.AppendText(DateTime.Now.ToString("HH:mm:ss")+" "+text+Environment.NewLine);}
    void RefreshProfiles()
    {
        Directory.CreateDirectory(directory);files.Clear();profiles.Items.Clear();
        var candidates=Directory.GetFiles(directory,"*.json").ToList();
        string legacy=Path.Combine(Path.GetDirectoryName(directory)!,"agent.json");if(File.Exists(legacy))candidates.Add(legacy);
        foreach(var path in candidates)
        {try{var config=ConnectedAgent.LoadProtected<PairedConfig>(path);files.Add(path);profiles.Items.Add(new Uri(config.ServerUrl).Authority+" · conexão "+Path.GetFileNameWithoutExtension(path)[..Math.Min(8,Path.GetFileNameWithoutExtension(path).Length)]);}catch(Exception){Append("Uma conexão salva não pôde ser aberta. Faça novo pareamento dessa empresa.");}}
        if(profiles.Items.Count>0)profiles.SelectedIndex=0;
    }
    void Start(string path)
    {
        if(running.TryGetValue(path,out var prior)&&!prior.HasExited)return;
        var info=new ProcessStartInfo(Environment.ProcessPath!){UseShellExecute=false,CreateNoWindow=true,RedirectStandardOutput=true,RedirectStandardError=true};
        info.ArgumentList.Add("connect");info.ArgumentList.Add("--config");info.ArgumentList.Add(path);
        string label=new Uri(ConnectedAgent.LoadProtected<PairedConfig>(path).ServerUrl).Authority+" · conexão "+Path.GetFileNameWithoutExtension(path)[..Math.Min(8,Path.GetFileNameWithoutExtension(path).Length)];
        var child=new Process{StartInfo=info,EnableRaisingEvents=true};child.OutputDataReceived+=(_,e)=>{if(e.Data is not null)Receive(path,label,e.Data);};child.ErrorDataReceived+=(_,e)=>{if(e.Data is not null)Receive(path,label,e.Data);};
        child.Exited+=(_,_)=>Append("Uma conexão foi encerrada; confira o resultado acima.");child.Start();child.BeginOutputReadLine();child.BeginErrorReadLine();running[path]=child;
    }
    void Receive(string path,string label,string data)
    {
        if(IsDisposed)return;
        if(InvokeRequired){BeginInvoke(()=>Receive(path,label,data));return;}
        const string prefix="DOCPRONTO_STATUS:";
        if(data.StartsWith(prefix,StringComparison.Ordinal))
        {
            try{var status=JsonSerializer.Deserialize<AgentStatus>(data[prefix.Length..],ConnectedAgent.Json);if(status is not null){statuses[path]=status;RenderCertificates();}}catch(JsonException){Append("Não foi possível atualizar a lista de certificados.");}
            return;
        }
        Append(label+": "+data);
    }
    void RenderCertificates()
    {
        certificates.BeginUpdate();bindings.BeginUpdate();certificates.Items.Clear();bindings.Items.Clear();
        try
        {
            if(profiles.SelectedIndex<0||!statuses.TryGetValue(files[profiles.SelectedIndex],out var status)){inventoryCount.Text="Aguardando a conexão enviar os certificados...";return;}
            string search=certificateSearch.Text.Trim();
            foreach(var cert in status.Certificates.OrderBy(c=>c.Subject))
            {
                string title=cert.Subject.Split(',')[0];if(title.StartsWith("CN="))title=title[3..];
                string ending=cert.Thumbprint.Length>8?cert.Thumbprint[^8..]:cert.Thumbprint;
                string row=$"{title} · final {ending} · {cert.Store} · {(cert.HasPrivateKey?"com chave privada":"sem chave privada")}";
                if(row.Contains(search,StringComparison.OrdinalIgnoreCase))certificates.Items.Add(row);
            }
            foreach(var binding in status.Bindings.OrderBy(b=>b.CompanyName))
                bindings.Items.Add($"{binding.CompanyName} · {binding.Document} → final {binding.Thumbprint[^Math.Min(8,binding.Thumbprint.Length)..]} · {binding.Store}");
            inventoryCount.Text=$"{certificates.Items.Count} exibidos de {status.Certificates.Count} certificados. Seleção por empresa feita no site.";
        }
        finally{certificates.EndUpdate();bindings.EndUpdate();}
    }
    void Stop(string path){if(running.Remove(path,out var child)){if(!child.HasExited)child.Kill();child.Dispose();Append("Conexão pausada.");}}
}
