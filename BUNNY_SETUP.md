# Nastavení Bunny.net pro klientské reporty

1. V Bunny.net otevřete **Storage** a vytvořte Storage Zone v evropském regionu.
2. V kartě **Access → API / HTTP** zkopírujte název zóny, heslo/Access Key a regionální endpoint.
3. Vytvořte **Pull Zone**, jako origin zvolte vytvořenou Storage Zone a poznamenejte si její adresu `https://…b-cdn.net`.
4. V Pull Zone otevřete **Security**, zapněte **Token Authentication** a zkopírujte Token Authentication Key.
5. Doplňte hodnoty do `.env` podle `.env.example` a aplikaci restartujte.

Storage Access Key a Token Authentication Key jsou rozdílné údaje. Nikdy je nevkládejte do reportu ani neposílejte klientovi.

Publikace používá adresářový Advanced Token Authentication odkaz. Odkaz i fyzická retence mají ve výchozím nastavení 100 dní. Úklid probíhá při startu, po publikaci a jednou denně, dokud aplikace běží.
