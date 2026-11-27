function [runs, runFolder] = MS3_batch(parameters, assumedC, outputRoot, progress)
% Simulate the M3 matrix and write one CSV per trace plus a fit summary.
% Each invocation creates a separate batch in outputRoot.
if nargin < 4, progress = []; end
validateattributes(assumedC,{'double'},{'scalar','real','finite','nonnegative'});
assert(isequal(sort(parameters.bolt_count(:)),[1;2;3]), ...
    'Expected one parameter row per bolt configuration.');
boltCount = repelem((1:3)',21);
distance = repmat((50:70)',3,1);
n = numel(distance);
file = compose("B%d_D%03d.csv",boltCount,distance);
stopTime = zeros(n,1); peaks = zeros(n,1); note = strings(n,1);
inertia = zeros(n,1); model = nan(n,3); fit = nan(n,3);

if ~isfolder(outputRoot), mkdir(outputRoot); end
% Publish the completed directory in one rename. An interrupted export
% stays visibly marked as incomplete and cannot overwrite a previous batch.
stamp = string(datetime('now','Format','yyyyMMdd_HHmmss_SSS'));
runFolder = fullfile(outputRoot,"batch_"+stamp);
if isfolder(runFolder) || isfolder(runFolder+".partial")
    [~,uniqueId] = fileparts(tempname(outputRoot));
    runFolder = runFolder+"_"+string(uniqueId);
end
staging = runFolder+".partial";
[ok,msg,msgId] = mkdir(staging);
if ~ok || ~isempty(msgId), error('MS3:BatchFolder','Cannot start batch at %s. %s',staging,msg); end
try
    for k = 1:n
        config = parameters(parameters.bolt_count==boltCount(k),:);
        trace = MS3_simulate_case(config,distance(k),assumedC);
        if any(~isfinite(trace.angle_deg))
            error('MS3:BatchNumerics','Non-finite simulated angle in %s.',file(k));
        end
        info = trace.Properties.UserData;
        stopTime(k) = info.stopTime_s;
        inertia(k) = config.Ip_kg_m2;
        peaks(k) = info.peakCount;
        note(k) = string(info.fitNote);
        model(k,:) = info.damping.Model.';
        fit(k,:) = info.damping.SwingFit.';
        writetable(trace,fullfile(staging,file(k)));
        if ~isempty(progress) && (mod(k,5)==0 || k==n)
            progress(k,n);
        end
    end
    runs = table(file,boltCount,distance,stopTime,inertia,peaks, ...
        model(:,2),model(:,1),model(:,3),fit(:,3),fit(:,1),fit(:,2),note, ...
        'VariableNames',{'file','bolt_count','distance_mm','cart_stop_s','Ip_kg_m2', ...
        'free_peak_count','assumed_c_Nm_s_rad','model_b_s_inv','model_lambda_s_inv', ...
        'fit_lambda_s_inv','fit_b_s_inv','fit_c_Nm_s_rad','fit_note'});
    writetable(runs,fullfile(staging,'summary.csv'));
    writetable(MS3_plotting_table(runs),fullfile(staging,'summary_plotting.csv'),'Encoding','UTF-8');
    [ok,msg] = movefile(staging,runFolder);
    if ~ok, error('MS3:BatchPublish','%s',msg); end
catch issue
    error('MS3:BatchIncomplete','Batch incomplete in %s. %s',staging,issue.message);
end
end
