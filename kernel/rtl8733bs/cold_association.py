"""Exact cold teardown delta: invalidate driver roaming before OS disconnect."""
DECL='\tstruct pwrctrl_priv *pwrpriv = adapter_to_pwrctl(padapter);\n'
OLD='\tif (rtw_chk_roam_flags(padapter, RTW_ROAM_ON_RESUME)) {'
NEW='''\t/* Cold teardown destroys firmware association and keys. Report the
\t * disconnect to cfg80211/supplicant instead of claiming a retained roam.
\t * Keep the existing roaming behavior for firmware-retaining WoW paths.
\t */
\tif (!pwrpriv->wowlan_mode && !pwrpriv->wowlan_ap_mode)
\t\trtw_set_to_roam(padapter, 0);
\telse if (rtw_chk_roam_flags(padapter, RTW_ROAM_ON_RESUME)) {'''

def transformed(body):
    anchor='\tstruct mlme_priv *pmlmepriv = &padapter->mlmepriv;\n'
    if body.count(anchor)!=1 or body.count(OLD)!=1 or DECL in body:raise ValueError('exact original suspend teardown body required')
    return body.replace(anchor,anchor+DECL).replace(OLD,NEW)

def projection(before,after):
    from prepare_cached_ps import function
    old=function(before,'rtw_suspend_free_assoc_resource');new=function(after,'rtw_suspend_free_assoc_resource')
    if new!=transformed(old) or after.replace(new,old,1)!=before:raise ValueError('only exact cold-disconnect lifecycle delta admitted')
