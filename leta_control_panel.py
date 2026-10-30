# -*- coding: utf-8 -*-
"""LETA Control Panel для LT-3264. Python 3.9+, Tkinter, стандартная библиотека."""
import re
import socket
import struct
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox

TEXT_PORT = 27923
AT_PORT = 22224
W, H = 64, 32

# Демонстрационные координаты из описания протокола. Не являются настройками конкретного табло.
SAMPLE = {
    1: (0, 0, 45, 9), 2: (45, 0, 64, 16),
    3: (0, 9, 45, 32), 4: (45, 16, 64, 32),
    5: (0, 1, 64, 10), 6: (0, 10, 64, 32),
    7: (0, 1, 64, 22), 8: (0, 23, 64, 32),
}

def crc16_modbus(data):
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc

def text_packet(text, flags=0, ha=2, va=2, msg=1):
    payload = bytes((0x74, flags & 255, ha & 255, va & 255))
    payload += text.encode("cp1251", errors="replace")
    body = struct.pack("<H", msg) + payload
    core = struct.pack("<H", 2 + 2 + len(payload) + 2) + body
    return b"\x02\x02" + core + struct.pack("<H", crc16_modbus(core)) + b"\x03"

class App:
    def __init__(self, root):
        self.root = root
        root.title("LETA Control Panel — LT-3264")
        root.geometry("1080x760")
        root.minsize(900, 650)
        self.zones = dict(SAMPLE)
        self.zone = tk.IntVar(value=1)
        self.drag_start = None
        self.drag_item = None
        self._ui()
        self.draw()

    def log(self, text):
        self.logbox.configure(state="normal")
        self.logbox.insert("end", time.strftime("[%H:%M:%S] ") + text + "\\n")
        self.logbox.see("end")
        self.logbox.configure(state="disabled")

    def _ui(self):
        conn = ttk.LabelFrame(self.root, text="Подключение")
        conn.pack(fill="x", padx=9, pady=7)
        self.ip = tk.StringVar(value="192.168.31.215")
        self.tp = tk.StringVar(value=str(TEXT_PORT))
        self.ap = tk.StringVar(value=str(AT_PORT))
        self.timeout = tk.StringVar(value="1.2")
        for i, (label, var, width) in enumerate((
            ("IP табло", self.ip, 17), ("Порт текста", self.tp, 7),
            ("Порт AT", self.ap, 7), ("Тайм-аут, с", self.timeout, 6))):
            ttk.Label(conn, text=label).grid(row=0, column=i*2, padx=(7,2), pady=7)
            ttk.Entry(conn, textvariable=var, width=width).grid(row=0, column=i*2+1, padx=(0,8))
        ttk.Button(conn, text="Запросить зоны", command=self.query_zones).grid(row=0, column=8, padx=5)

        tabs = ttk.Notebook(self.root)
        tabs.pack(fill="both", expand=True, padx=9, pady=3)
        self.tab_text, self.tab_zones, self.tab_at = ttk.Frame(tabs), ttk.Frame(tabs), ttk.Frame(tabs)
        tabs.add(self.tab_text, text="Вывод текста")
        tabs.add(self.tab_zones, text="Редактор зон")
        tabs.add(self.tab_at, text="AT-команды")
        self._text_ui()
        self._zones_ui()
        self._at_ui()

        lf = ttk.LabelFrame(self.root, text="Журнал отправки и ответов")
        lf.pack(fill="both", padx=9, pady=7)
        self.logbox = tk.Text(lf, height=8, state="disabled", font=("Consolas", 9), wrap="word")
        sb = ttk.Scrollbar(lf, command=self.logbox.yview)
        self.logbox.configure(yscrollcommand=sb.set)
        self.logbox.pack(side="left", fill="both", expand=True, padx=4, pady=4)
        sb.pack(side="right", fill="y", pady=4)

    def _text_ui(self):
        f = ttk.Frame(self.tab_text, padding=14); f.pack(fill="both", expand=True)
        ttk.Label(f, text="Произвольный текст:", font=("Segoe UI", 11, "bold")).pack(anchor="w")
        self.text = tk.Text(f, height=5, font=("Segoe UI", 18))
        self.text.pack(fill="x", pady=8); self.text.insert("1.0", "ТЕСТ ТАБЛО")
        row = ttk.Frame(f); row.pack(fill="x", pady=5)
        self.flags, self.ha, self.va, self.msg = (tk.StringVar(value=x) for x in ("0","2","2","1"))
        for label, var, vals in (
            ("Flags", self.flags, None), ("Выравнивание X (ha)", self.ha, ("0","1","2")),
            ("Выравнивание Y (va)", self.va, ("0","1","2")), ("№ пакета", self.msg, None)):
            ttk.Label(row, text=label).pack(side="left", padx=(5,2))
            if vals:
                ttk.Combobox(row, textvariable=var, values=vals, width=6, state="readonly").pack(side="left")
            else:
                ttk.Entry(row, textvariable=var, width=7).pack(side="left")
        ttk.Button(f, text="Отправить текст", command=self.send_text).pack(anchor="w", pady=12)
        ttk.Label(f, text=("Отправка использует известный пакет 0x74, CRC16-Modbus и CP1251. "
                           "В предоставленном описании нет подтверждённого параметра выбора зоны "
                           "для текстового пакета; здесь настраивается геометрия зон."),
                  wraplength=900, foreground="#555").pack(anchor="w")

    def _zones_ui(self):
        outer = ttk.Frame(self.tab_zones, padding=9); outer.pack(fill="both", expand=True)
        left = ttk.Frame(outer); left.pack(side="left", fill="both", expand=True)
        ttk.Label(left, text="Сетка 64 × 32. Выберите зону справа и нарисуйте её прямоугольник мышью.",
                  font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(0,6))
        self.canvas = tk.Canvas(left, bg="white", width=760, height=430,
                                highlightthickness=1, highlightbackground="#888")
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Button-1>", self.down)
        self.canvas.bind("<B1-Motion>", self.motion)
        self.canvas.bind("<ButtonRelease-1>", self.up)

        r = ttk.LabelFrame(outer, text="Выбранная зона", padding=9)
        r.pack(side="right", fill="y", padx=(10,0))
        ttk.Label(r, text="Номер зоны").grid(row=0,column=0,sticky="w",pady=4)
        cb=ttk.Combobox(r,textvariable=self.zone,values=tuple(range(1,9)),width=7,state="readonly")
        cb.grid(row=0,column=1,pady=4); cb.bind("<<ComboboxSelected>>",lambda e:self.load_fields())
        self.coords={}
        for n,(label,key) in enumerate((("X начало","x1"),("Y начало","y1"),("X конец","x2"),("Y конец","y2")),1):
            ttk.Label(r,text=label).grid(row=n,column=0,sticky="w",pady=4)
            v=tk.StringVar(); self.coords[key]=v
            ttk.Entry(r,textvariable=v,width=8).grid(row=n,column=1,pady=4)
        ttk.Button(r,text="Применить координаты",command=self.apply_fields).grid(row=5,column=0,columnspan=2,sticky="ew",pady=(10,4))
        ttk.Button(r,text="Отправить размер зоны",command=self.apply_zone).grid(row=6,column=0,columnspan=2,sticky="ew",pady=4)
        ttk.Button(r,text="Запросить зоны AT+IM=4",command=self.query_zones).grid(row=7,column=0,columnspan=2,sticky="ew",pady=(13,4))
        ttk.Button(r,text="Загрузить пример 1–8",command=self.load_sample).grid(row=8,column=0,columnspan=2,sticky="ew",pady=4)
        ttk.Label(r,text="Команда:",).grid(row=9,column=0,columnspan=2,sticky="w",pady=(14,3))
        self.cmdvar=tk.StringVar()
        ttk.Entry(r,textvariable=self.cmdvar,state="readonly",width=29).grid(row=10,column=0,columnspan=2)
        self.load_fields()

    def _at_ui(self):
        f=ttk.Frame(self.tab_at,padding=15); f.pack(fill="both",expand=True)
        presets=[
            ("Системная информация","at+td=1,0"),
            ("Показ логотипа","at+td=1,1"),
            ("Тест индикации","at+td=1,2"),
            ("Запрос состояния","at+fa=0,9"),
            ("Запрос зон","at+im=4"),
            ("Выключить индикацию","at+im=0,0,0"),
        ]
        for i,(label,cmd) in enumerate(presets):
            ttk.Button(f,text=label,command=lambda c=cmd:self.send_at(c)).grid(
                row=i//2,column=i%2,sticky="ew",padx=5,pady=5,ipady=5)
        ttk.Button(f,text="Перезагрузка табло",command=self.reboot).grid(
            row=3,column=0,sticky="ew",padx=5,pady=5,ipady=5)
        ttk.Separator(f).grid(row=4,column=0,columnspan=2,sticky="ew",pady=15)
        ttk.Label(f,text="Своя AT-команда (например, at+fa=0,9):").grid(row=5,column=0,columnspan=2,sticky="w")
        self.raw=tk.StringVar(value="at+fa=0,9")
        ttk.Entry(f,textvariable=self.raw).grid(row=6,column=0,sticky="ew",padx=5,pady=7)
        ttk.Button(f,text="Отправить",command=lambda:self.send_at(self.raw.get())).grid(row=6,column=1,sticky="ew",padx=5)
        ttk.Label(f,text="Перезагрузка отправляет at+fa=5, затем at+im=0,0,0.",
                  foreground="#555").grid(row=7,column=0,columnspan=2,sticky="w",pady=12)
        f.columnconfigure(0,weight=1); f.columnconfigure(1,weight=1)

    def connection(self):
        ip=self.ip.get().strip()
        if not ip: raise ValueError("Укажите IP табло")
        return ip,int(self.tp.get()),int(self.ap.get()),float(self.timeout.get())

    def udp(self,payload,port,reply=False):
        try: ip,_,_,timeout=self.connection()
        except Exception as e: messagebox.showerror("Подключение",str(e)); return
        def work():
            try:
                with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as s:
                    s.settimeout(timeout); s.sendto(payload,(ip,port))
                    self.root.after(0,self.log,f"TX {ip}:{port}: {payload!r}")
                    if reply:
                        end=time.monotonic()+max(timeout, 1.8); got=[]
                        while time.monotonic()<end:
                            try:
                                data,addr=s.recvfrom(4096); got.append(data)
                                self.root.after(0,self.log,f"RX {addr}: {data!r}")
                            except socket.timeout: break
                        if got: self.root.after(0,self.parse_zone_reply,got)
                        else: self.root.after(0,self.log,"Ответ не получен (тайм-аут).")
            except Exception as e:
                self.root.after(0,self.log,f"ОШИБКА: {e}")
        threading.Thread(target=work,daemon=True).start()

    def send_at(self,cmd):
        cmd=cmd.strip()
        if not cmd.lower().startswith("at+"):
            messagebox.showwarning("AT-команда","Команда должна начинаться с at+"); return
        try: _,_,port,_=self.connection()
        except Exception as e: messagebox.showerror("Подключение",str(e)); return
        self.udp((cmd+"\r\n").encode("ascii",errors="replace"),port,True)

    def reboot(self):
        if messagebox.askyesno("Перезагрузка","Отправить команды перезагрузки?"):
            self.send_at("at+fa=5")
            self.root.after(350,lambda:self.send_at("at+im=0,0,0"))

    def send_text(self):
        try:
            _,port,_,_=self.connection()
            packet=text_packet(self.text.get("1.0","end-1c"),int(self.flags.get(),0),
                               int(self.ha.get()),int(self.va.get()),int(self.msg.get(),0))
            self.udp(packet,port,False)
        except Exception as e: messagebox.showerror("Текст",str(e))

    def coords_value(self):
        x1,y1,x2,y2=(int(self.coords[k].get()) for k in ("x1","y1","x2","y2"))
        if not (0<=x1<x2<=W and 0<=y1<y2<=H):
            raise ValueError("Допустимые координаты: 0 ≤ X1 < X2 ≤ 64; 0 ≤ Y1 < Y2 ≤ 32")
        return x1,y1,x2,y2

    def load_fields(self):
        z=int(self.zone.get()); vals=self.zones.get(z,(0,0,64,32))
        for k,v in zip(("x1","y1","x2","y2"),vals): self.coords[k].set(str(v))
        self.update_cmd(); self.draw()

    def update_cmd(self):
        try:
            x1,y1,x2,y2=self.coords_value()
            self.cmdvar.set(f"at+im=4,{int(self.zone.get())},{x1},{y1},{x2},{y2}")
        except Exception: self.cmdvar.set("Проверьте координаты")

    def apply_fields(self):
        z=int(self.zone.get()); self.zones[z]=self.coords_value()
        self.update_cmd(); self.draw()

    def apply_zone(self):
        try:
            self.apply_fields()
            x1,y1,x2,y2=self.zones[int(self.zone.get())]
            self.send_at(f"at+im=4,{int(self.zone.get())},{x1},{y1},{x2},{y2}")
        except Exception as e: messagebox.showerror("Зона",str(e))

    def query_zones(self): self.send_at("at+im=4")

    def parse_zone_reply(self,replies):
        # Ответ AT-консоли табло декодируем в CP866.
        raw=b"\\n".join(replies).decode("cp866",errors="replace")
        self.log("Ответ AT (CP866):")
        for line in raw.replace("\\r", "").split("\\n"):
            line=line.strip()
            if line:
                self.log("  " + line)

        found={}
        full_screen=None
        for line in raw.replace("\\r", "").split("\\n"):
            m=re.search(r"<\\s*(\\d+)\\s+(-?\\d+)\\s+(-?\\d+)\\s+(-?\\d+)\\s+(-?\\d+)",line)
            if not m:
                continue
            z,x1,y1,x2,y2=map(int,m.groups())
            coords=(x1,y1,x2,y2)
            if z == 0:
                if 0 <= x1 < x2 <= 64 and 0 <= y1 < y2 <= 32:
                    full_screen=coords
                continue
            if 1 <= z <= 8 and 0 <= x1 < x2 <= 64 and 0 <= y1 < y2 <= 32:
                found[z]=coords

        if found:
            self.zones.update(found)
            self.load_fields()
            self.log("Координаты зон обновлены из табло: " +
                     ", ".join(str(z) for z in sorted(found)))
            if full_screen:
                self.log(f"Полноэкранная область: {full_screen}")
            if "OK:at+im" in raw:
                self.log("Табло подтвердило команду: OK:at+im")
        else:
            self.log("Координаты зон в ответе не найдены. Проверьте строки ответа выше.")

    def load_sample(self):
        if messagebox.askyesno("Шаблон","Загрузить демонстрационные координаты из описания?"):
            self.zones=dict(SAMPLE); self.load_fields()
            self.log("Загружен пример зон. Это не прочитанные настройки табло.")

    def geometry(self):
        cw=max(self.canvas.winfo_width(),700); ch=max(self.canvas.winfo_height(),400)
        scale=min((cw-55)/W,(ch-55)/H)
        return 25,22,scale

    def draw(self):
        if not hasattr(self,"canvas"): return
        c=self.canvas; c.delete("all"); ox,oy,s=self.geometry()
        xend,yend=ox+W*s,oy+H*s
        for x in range(W+1):
            xx=ox+x*s; c.create_line(xx,oy,xx,yend,fill="#ddd" if x%4 else "#aaa")
        for y in range(H+1):
            yy=oy+y*s; c.create_line(ox,yy,xend,yy,fill="#ddd" if y%4 else "#aaa")
        c.create_rectangle(ox,oy,xend,yend,outline="black",width=2)
        c.create_text(ox,oy-10,text="0",anchor="sw")
        c.create_text(xend,oy-10,text="64",anchor="se")
        c.create_text(ox-7,yend,text="32",anchor="e")
        colors=("#fff45c","#c8e2b8","#b9cbea","#f7dfd2","#8bd04f","#ffe39a","#bfd9ef","#ffe39a")
        for z,(x1,y1,x2,y2) in sorted(self.zones.items()):
            selected=z==int(self.zone.get())
            xa,ya,xb,yb=ox+x1*s,oy+y1*s,ox+x2*s,oy+y2*s
            c.create_rectangle(xa,ya,xb,yb,fill=colors[(z-1)%8],
                               outline="#111" if selected else "#666",width=3 if selected else 1)
            c.create_text((xa+xb)/2,(ya+yb)/2,text=str(z),font=("Segoe UI",11,"bold"))

    def point(self,event):
        ox,oy,s=self.geometry()
        return max(0,min(64,round((event.x-ox)/s))),max(0,min(32,round((event.y-oy)/s)))

    def down(self,event):
        self.drag_start=self.point(event)

    def motion(self,event):
        if not self.drag_start: return
        if self.drag_item: self.canvas.delete(self.drag_item)
        x1,y1=self.drag_start; x2,y2=self.point(event); ox,oy,s=self.geometry()
        self.drag_item=self.canvas.create_rectangle(ox+x1*s,oy+y1*s,ox+x2*s,oy+y2*s,
                                                     outline="red",width=2,dash=(4,3))

    def up(self,event):
        if not self.drag_start: return
        x1,y1=self.drag_start; x2,y2=self.point(event); self.drag_start=None
        if self.drag_item: self.canvas.delete(self.drag_item); self.drag_item=None
        x1,x2=sorted((x1,x2)); y1,y2=sorted((y1,y2))
        if x1==x2 or y1==y2: return
        self.zones[int(self.zone.get())]=(x1,y1,x2,y2)
        self.load_fields()

if __name__=="__main__":
    root=tk.Tk()
    App(root)
    root.mainloop()
